"""Render pipeline orchestration.

run_render(task, job_pk) is the entry point scheduled by the task runner:

  1. sort clips by rank DESC (countdown: #5 first, #1 last)
  2. normalize every clip (one ffmpeg run each)  — progress 5→80
  3. concat + master title + optional BGM ducking — progress 80→97
  4. probe, move to MEDIA_ROOT/renders/<uuid8>.mp4 — progress 97→100
"""
import logging
import shutil
import subprocess
import uuid
from pathlib import Path

from django.conf import settings

from apps.bgm.models import BgmTrack
from apps.core import runner
from apps.renders.models import RenderJob
from apps.videos.models import Video
from apps.videos.services.probe import probe_media

from . import audio as audio_svc
from . import encoder as encoder_svc
from . import filters as filters_svc

logger = logging.getLogger(__name__)

# Progress windows (percent on the 0-100 task scale).
PHASE_NORMALIZE_START = 5.0
PHASE_NORMALIZE_END = 80.0
PHASE_ASSEMBLY_END = 97.0

FFMPEG = "ffmpeg"
STDERR_TAIL = 2000
CLIP_TIMEOUT = 1800  # seconds per per-clip ffmpeg run


class RenderError(Exception):
    """Fatal pipeline error surfaced on the Task as ``error``."""


def _run_plain(cmd, timeout=CLIP_TIMEOUT):
    """Run an ffmpeg command capturing output; returns (rc, stderr_tail)."""
    argv = [FFMPEG] + list(cmd)
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode, (proc.stderr or "")[-STDERR_TAIL:]
    except subprocess.TimeoutExpired:
        return 124, f"ffmpeg timed out after {timeout}s"
    except OSError as exc:
        return 127, f"failed to execute ffmpeg: {exc}"


# ---------------------------------------------------------------------------
# Per-clip normalization
# ---------------------------------------------------------------------------

def normalize_clip(video, clip, staging_dir, index):
    """Normalize one clip into staging_dir/clip_<index>.mp4.

    Returns (output_path, duration).  Raises RenderError on ffmpeg failure.
    """
    out_path = staging_dir / f"clip_{index}.mp4"
    start = float(clip["start"])
    end = float(clip["end"])
    title = clip.get("title") or video.title
    clip_settings = clip["_settings"]

    cmd = filters_svc.build_normalize_cmd(
        src_path=video.file.path,
        start=start,
        end=end,
        rank=clip["rank"],
        title=title,
        video_height_pct=clip_settings["video_height_pct"],
        background_blur=clip_settings["background_blur"],
        has_audio=video.has_audio,
        out_path=out_path,
        staging_dir=str(staging_dir),
    )

    rc, tail = _run_plain(cmd)
    if rc != 0:
        raise RenderError(
            f"Failed to normalize clip {index + 1} "
            f"({video.title!r} @ {start:.2f}-{end:.2f}s): "
            f"ffmpeg exit {rc}.\n{tail}"
        )
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RenderError(f"Normalization produced no output for clip {index + 1}")
    return out_path, end - start


# ---------------------------------------------------------------------------
# Final assembly
# ---------------------------------------------------------------------------

def _write_concat_list(paths, staging_dir):
    list_path = staging_dir / "list.txt"
    lines = []
    for p in paths:
        # Single-quote each path, escaping embedded single quotes.
        escaped = str(p).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list_path


def build_final_cmd(
    concat_list,
    master_title,
    bgm_track,
    bgm_volume,
    total_duration,
    out_path,
    staging_dir,
):
    """Assemble the final ffmpeg argv (without the binary).

    Video: concat demuxer input -> optional master-title drawtext -> yuv420p.
    Audio: dialogue passthrough, or the sidechain-ducked BGM mix.
    """
    inputs = ["-f", "concat", "-safe", "0", "-i", str(concat_list)]
    if bgm_track is not None:
        inputs += ["-stream_loop", "-1", "-i", str(bgm_track.file.path)]

    title_frag, _textfile = filters_svc.build_master_title_drawtext(
        master_title, str(staging_dir)
    )
    video_chain = f"[0:v]null{title_frag},format=yuv420p[vout]"

    if bgm_track is not None:
        audio_chain = audio_svc.build_bgm_chain(total_duration, bgm_volume)
    else:
        audio_chain = audio_svc.build_plain_audio_chain()

    filter_complex = video_chain + ";" + audio_chain

    cmd = ["-nostdin", "-hide_banner", "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
    ]
    # NVENC block; encoder.attempt_video_encode swaps it on fallback.
    cmd += list(encoder_svc.NVENC_ARGS)
    cmd += [
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-r", str(filters_svc.OUT_FPS),
        str(out_path),
    ]
    return cmd


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_render(task, job_pk):
    """Full render pipeline; returns the task result dict on success."""
    try:
        job = RenderJob.objects.get(pk=job_pk)
    except RenderJob.DoesNotExist:
        raise RenderError(f"RenderJob {job_pk} does not exist")

    payload = job.payload
    settings_dict = payload.get("settings", {})
    clips = list(payload.get("clips", []))
    master_title = payload.get("master_title", "") or ""

    renders_root = Path(settings.MEDIA_ROOT) / "renders"
    renders_root.mkdir(parents=True, exist_ok=True)
    staging_dir = renders_root / ".staging" / str(task.id)
    staging_dir.mkdir(parents=True, exist_ok=True)

    final_path = staging_dir / "final.mp4"

    try:
        # ---------------- ordering: rank DESC (countdown) ----------------
        clips.sort(key=lambda c: c["rank"], reverse=True)

        # ---------------- BGM resolution (fail hard: user picked it) -----
        bgm_track = None
        bgm_id = settings_dict.get("bgm_id")
        if bgm_id is not None:
            try:
                bgm_track = BgmTrack.objects.get(pk=bgm_id)
            except BgmTrack.DoesNotExist:
                raise RenderError(f"BGM track {bgm_id} does not exist")
            if not bgm_track.file or not Path(bgm_track.file.path).is_file():
                raise RenderError(
                    f"BGM file for track {bgm_track.name!r} is missing on disk"
                )

        # ---------------- phase 1: normalize clips (5→80) ----------------
        videos = {}
        for clip in clips:
            vid = clip["video_id"]
            if vid not in videos:
                try:
                    videos[vid] = Video.objects.get(pk=vid)
                except Video.DoesNotExist:
                    raise RenderError(f"Video {vid} disappeared before render")

        # Attach resolved settings once so builders see uniform data.
        for clip in clips:
            clip["_settings"] = settings_dict

        normalized_paths = []
        total_duration = 0.0
        n_clips = len(clips)
        span = PHASE_NORMALIZE_END - PHASE_NORMALIZE_START
        for i, clip in enumerate(clips):
            runner.set_progress(
                task.id, PHASE_NORMALIZE_START + span * (i / n_clips)
            )
            out_path, dur = normalize_clip(video=videos[clip["video_id"]],
                                           clip=clip, staging_dir=staging_dir,
                                           index=i)
            normalized_paths.append(out_path)
            total_duration += dur
            runner.set_progress(
                task.id, PHASE_NORMALIZE_START + span * ((i + 1) / n_clips)
            )

        # ---------------- phase 2: assembly (80→97) ----------------------
        runner.set_progress(task.id, PHASE_NORMALIZE_END)
        concat_list = _write_concat_list(normalized_paths, staging_dir)
        final_cmd = build_final_cmd(
            concat_list=concat_list,
            master_title=master_title,
            bgm_track=bgm_track,
            bgm_volume=settings_dict.get("bgm_volume", 0.4),
            total_duration=total_duration,
            out_path=final_path,
            staging_dir=staging_dir,
        )

        def on_progress(seconds_done):
            frac = seconds_done / total_duration if total_duration else 0.0
            runner.set_progress(
                task.id,
                PHASE_NORMALIZE_END
                + (PHASE_ASSEMBLY_END - PHASE_NORMALIZE_END) * min(frac, 1.0),
            )

        encoder_used = encoder_svc.attempt_video_encode(
            final_cmd, on_progress=on_progress, total_duration=total_duration
        )
        job.encoder_used = encoder_used
        job.save(update_fields=["encoder_used"])

        # ---------------- phase 3: probe + finalize (97→100) -------------
        runner.set_progress(task.id, PHASE_ASSEMBLY_END)
        facts = probe_media(final_path)
        if facts["width"] != filters_svc.OUT_W or facts["height"] != filters_svc.OUT_H:
            raise RenderError(
                f"Output has unexpected dimensions "
                f"{facts['width']}x{facts['height']} "
                f"(expected {filters_svc.OUT_W}x{filters_svc.OUT_H})"
            )
        if not facts["has_audio"]:
            raise RenderError("Output is missing its audio stream")

        filename = f"{uuid.uuid4().hex[:8]}.mp4"
        dest = renders_root / filename
        shutil.move(str(final_path), str(dest))

        job.output_file = filename
        job.save(update_fields=["output_file"])

        runner.set_progress(task.id, 100.0)

        return {
            "download_url": f"/media/renders/{filename}",
            "preview_url": f"/media/renders/{filename}",
            "output_file": filename,
            "encoder_used": encoder_used,
            "duration": facts["duration"],
        }
    finally:
        # Staging is always cleaned, success or failure.
        shutil.rmtree(staging_dir, ignore_errors=True)
        try:
            # Drop the .staging root too when this was its last tenant.
            staging_dir.parent.rmdir()
        except OSError:
            pass
