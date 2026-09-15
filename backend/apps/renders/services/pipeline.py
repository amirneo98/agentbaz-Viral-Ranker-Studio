"""Render pipeline orchestration (v1.2).

run_render(task, job_pk) is the entry point scheduled by the task runner:

  1. sort clips by rank DESC (countdown: #5 first, #1 last)
  2. normalize every clip (one ffmpeg run each)
     - fetch-heavy jobs: progress 5→80   (v1.1 weights)
     - all-HD-section jobs: progress 0→70 (no fetch phase)
  3. concat + master title + optional BGM ducking — progress →97
  4. probe, move to MEDIA_ROOT/renders/<uuid8>.mp4 — progress 97→100

v1.2 request shape (all additions optional / backward compatible):

  {
    "master_title": str,
    "master_title_style": {font, fontSize, textColor, strokeColor,
                           strokeWidth, shadow, badgeBg, badgeOpacity,
                           textPosition, margin},
    "aspect": "9:16" | "16:9",
    "clips": [
      {
        "clip_id" | "video_id": int,
        "rank": int, "start": float, "end": float,
        "title": str, "subtitle": str,
        "style": {…same keys as master_title_style…},
        "volume": float (0..3),
        "hd_file": str  # pre-downloaded section mp4 (absolute or media-rel)
      }
    ],
    "settings": {
      "video_height_pct" | "video_scale_pct": 30..100,
      "background_blur": bool, "blur_sigma": float,
      "bgm_id": int|null, "bgm_volume": 0..1,
      "ducking_threshold": float, "ducking_ratio": float
    }
  }

v1.1 payloads (video_id clips, settings without the new keys) take the
same path with v1.1 defaults, so previously stored jobs render identically.
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

# All-HD jobs skip the fetch phase entirely: normalize 0→70, concat 70→100.
HD_NORMALIZE_START = 0.0
HD_NORMALIZE_END = 70.0

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
# v1.2 request coercion
# ---------------------------------------------------------------------------

def _as_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_hd_path(raw):
    """Resolve an hd_file reference to a Path, or None when unusable.

    Accepts absolute paths and paths relative to MEDIA_ROOT (the studio
    stores media-relative paths).  Missing files log a warning and fall
    back to the video + trim path rather than failing the render.
    """
    if not raw:
        return None
    raw = str(raw)
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = Path(settings.MEDIA_ROOT) / raw
    if candidate.is_file():
        return candidate
    logger.warning("hd_file %r does not exist; falling back to trim", raw)
    return None


def coerce_v12_payload(payload):
    """Normalize a stored payload to the v1.2 internal shape.

    Returns (aspect, clips, settings_dict, master_title, master_style) where
    each clip dict carries: src_path (hd) or video_id, rank, start, end,
    title, subtitle, style, volume, duration_hint.
    """
    settings_dict = dict(payload.get("settings") or {})
    clips_in = list(payload.get("clips") or [])

    aspect = filters_svc.normalize_aspect(payload.get("aspect")
                                          or settings_dict.get("aspect"))
    master_title = payload.get("master_title", "") or ""
    master_style = payload.get("master_title_style") or None

    # settings coercion: v1.2 names + v1.1 fallbacks
    scale_pct = settings_dict.get("video_scale_pct",
                                  settings_dict.get("video_height_pct", 80))
    try:
        scale_pct = int(scale_pct)
    except (TypeError, ValueError):
        scale_pct = 80
    background_blur = bool(settings_dict.get("background_blur", True))
    blur_sigma = _as_float(settings_dict.get("blur_sigma"),
                           filters_svc.DEFAULT_BLUR_SIGMA)
    bgm_volume = _as_float(settings_dict.get("bgm_volume"), 0.4)
    ducking_threshold = _as_float(
        settings_dict.get("ducking_threshold"),
        audio_svc.DEFAULT_DUCKING_THRESHOLD,
    )
    ducking_ratio = _as_float(
        settings_dict.get("ducking_ratio"), audio_svc.DEFAULT_DUCKING_RATIO
    )
    settings_out = {
        "video_height_pct": scale_pct,
        "video_scale_pct": scale_pct,
        "background_blur": background_blur,
        "blur_sigma": blur_sigma,
        "bgm_id": settings_dict.get("bgm_id"),
        "bgm_volume": bgm_volume,
        "ducking_threshold": ducking_threshold,
        "ducking_ratio": ducking_ratio,
        "aspect": aspect,
    }

    clips_out = []
    for clip in clips_in:
        hd_path = _resolve_hd_path(clip.get("hd_file"))
        if hd_path is None and clip.get("video_id") is None:
            # Studio clip without a fetched HD section: fall back to a
            # server-side full download of its source_url (v1.1 path),
            # reusing the fetch service. This runs at render time inside
            # the background task, so request threads stay non-blocking.
            source_url = clip.get("source_url")
            if not source_url:
                raise RenderError(
                    "clip has neither video_id, hd_file, nor source_url"
                )
            from apps.videos.services import fetch as fetch_svc

            tmp_dir = Path(settings.MEDIA_ROOT) / "temp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            from apps.videos.services.downloader import YtDlpDownloader

            result = YtDlpDownloader().download(source_url, tmp_dir)
            media = fetch_svc._ensure_mp4(result.path)
            hd_path = media
            # full download: honor the trim window
            start = _as_float(clip.get("start"), 0.0)
            end = _as_float(clip.get("end"))
            needs_trim = True
        else:
            start = clip.get("start")
            end = clip.get("end")
            needs_trim = False
        if hd_path is not None:
            duration_hint = _as_float(clip.get("duration_hint"))
            if duration_hint is None:
                try:
                    duration_hint = probe_media(hd_path)["duration"]
                except Exception:
                    duration_hint = None
        else:
            duration_hint = None
        clips_out.append({
            "video_id": clip.get("video_id"),
            "hd_path": hd_path,
            "needs_trim": needs_trim,
            "rank": clip.get("rank", 1),
            "start": start,
            "end": end,
            "title": clip.get("title") or "",
            "subtitle": clip.get("subtitle") or "",
            "style": clip.get("style") or None,
            "volume": clip.get("volume"),
            "duration_hint": duration_hint,
            # v1.3: OpenReel text overlay descriptor (passthrough)
            "openreel_text": clip.get("openreel_text"),
        })
    if not clips_out:
        raise RenderError("payload has no clips")
    return aspect, clips_out, settings_out, master_title, master_style


# ---------------------------------------------------------------------------
# Per-clip normalization
# ---------------------------------------------------------------------------

def normalize_clip(video, clip, staging_dir, index, aspect, clip_settings):
    """Normalize one clip into staging_dir/clip_<index>.mp4.

    *video* may be None for HD-section clips.  Returns (output_path,
    duration).  Raises RenderError on ffmpeg failure.

    v1.3: a clip carrying ``openreel_text`` gets its overlay converted to
    animated drawtext fragment(s) here (inside the task thread).
    """
    out_path = staging_dir / f"clip_{index}.mp4"

    extra_drawtexts = None
    overlay = clip.get("openreel_text")
    if overlay and isinstance(overlay, dict) and overlay.get("text"):
        try:
            from . import openreel as openreel_svc

            frag, _tf = openreel_svc.build_animated_drawtext(
                text=overlay.get("text"),
                style=overlay.get("style"),
                transform=overlay.get("transform"),
                animation=overlay.get("animation"),
                aspect=aspect,
                staging_dir=str(staging_dir),
                clip_offset=float(overlay.get("offset") or 0.0),
                clip_duration=float(overlay.get("duration") or 0.0) or None,
                prefix=f"or_{index}_",
            )
            if frag:
                extra_drawtexts = [frag]
        except Exception as exc:  # noqa: BLE001 - overlay must not kill render
            logger.warning("OpenReel overlay conversion failed: %s", exc)

    if clip.get("hd_path") is not None:
        src_path = clip["hd_path"]
        if clip.get("needs_trim"):
            # full-download fallback: apply the trim window
            start = float(clip["start"] or 0.0)
            end = float(clip["end"]) if clip.get("end") else None
        else:
            start = end = None
        hd_facts = probe_media(src_path)
        has_audio = hd_facts["has_audio"]
        duration = clip.get("duration_hint") or hd_facts["duration"]
        title = clip.get("title")
    else:
        src_path = video.file.path
        start = float(clip["start"])
        end = float(clip["end"])
        has_audio = video.has_audio
        duration = end - start
        title = clip.get("title") or video.title

    cmd = filters_svc.build_normalize_cmd(
        src_path=src_path,
        start=start,
        end=end,
        rank=clip["rank"],
        title=title,
        video_height_pct=clip_settings["video_height_pct"],
        background_blur=clip_settings["background_blur"],
        has_audio=has_audio,
        out_path=out_path,
        staging_dir=str(staging_dir),
        aspect=aspect,
        blur_sigma=clip_settings.get("blur_sigma"),
        style=clip.get("style"),
        subtitle=clip.get("subtitle") or None,
        volume=clip.get("volume"),
        duration_hint=clip.get("duration_hint"),
        extra_drawtexts=extra_drawtexts,
    )

    rc, tail = _run_plain(cmd)
    if rc != 0:
        label = getattr(video, "title", None) or str(src_path)
        raise RenderError(
            f"Failed to normalize clip {index + 1} "
            f"({label!r}): ffmpeg exit {rc}.\n{tail}"
        )
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RenderError(f"Normalization produced no output for clip {index + 1}")
    return out_path, duration


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
    aspect="9:16",
    master_title_style=None,
    ducking_threshold=None,
    ducking_ratio=None,
):
    """Assemble the final ffmpeg argv (without the binary).

    Video: concat demuxer input -> optional master-title drawtext -> yuv420p.
    Audio: dialogue passthrough, or the sidechain-ducked BGM mix.
    """
    inputs = ["-f", "concat", "-safe", "0", "-i", str(concat_list)]
    if bgm_track is not None:
        inputs += ["-stream_loop", "-1", "-i", str(bgm_track.file.path)]

    title_frag, _textfile = filters_svc.build_master_title_drawtext(
        master_title, str(staging_dir), aspect=aspect,
        style=master_title_style,
    )
    video_chain = f"[0:v]null{title_frag},format=yuv420p[vout]"

    if bgm_track is not None:
        kwargs = {}
        if ducking_threshold is not None:
            kwargs["ducking_threshold"] = ducking_threshold
        if ducking_ratio is not None:
            kwargs["ducking_ratio"] = ducking_ratio
        audio_chain = audio_svc.build_bgm_chain(
            total_duration, bgm_volume, **kwargs
        )
    else:
        audio_chain = audio_svc.build_plain_audio_chain()

    filter_complex = video_chain + ";" + audio_chain

    # v1.2: 16:9 renders get the bitrate-hinted NVENC block.
    nvenc_args = (encoder_svc.NVENC_ARGS_169
                  if filters_svc.normalize_aspect(aspect) == "16:9"
                  else encoder_svc.NVENC_ARGS)

    cmd = ["-nostdin", "-hide_banner", "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
    ]
    # NVENC block; encoder.attempt_video_encode swaps it on fallback.
    cmd += list(nvenc_args)
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

    renders_root = Path(settings.MEDIA_ROOT) / "renders"
    renders_root.mkdir(parents=True, exist_ok=True)
    staging_dir = renders_root / ".staging" / str(task.id)
    staging_dir.mkdir(parents=True, exist_ok=True)

    final_path = staging_dir / "final.mp4"

    try:
        (aspect, clips, settings_dict,
         master_title, master_style) = coerce_v12_payload(payload)

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

        # ---------------- phase 1: normalize clips ----------------------
        all_hd = all(c["hd_path"] is not None for c in clips)
        if all_hd:
            phase_start, phase_end = HD_NORMALIZE_START, HD_NORMALIZE_END
        else:
            phase_start, phase_end = PHASE_NORMALIZE_START, PHASE_NORMALIZE_END

        videos = {}
        for clip in clips:
            vid = clip["video_id"]
            if vid is not None and vid not in videos:
                try:
                    videos[vid] = Video.objects.get(pk=vid)
                except Video.DoesNotExist:
                    raise RenderError(f"Video {vid} disappeared before render")

        normalized_paths = []
        total_duration = 0.0
        n_clips = len(clips)
        span = phase_end - phase_start
        for i, clip in enumerate(clips):
            runner.set_progress(
                task.id, phase_start + span * (i / n_clips)
            )
            out_path, dur = normalize_clip(
                video=videos.get(clip["video_id"]),
                clip=clip, staging_dir=staging_dir, index=i,
                aspect=aspect, clip_settings=settings_dict,
            )
            normalized_paths.append(out_path)
            total_duration += dur
            runner.set_progress(
                task.id, phase_start + span * ((i + 1) / n_clips)
            )

        # ---------------- phase 2: assembly ------------------------------
        runner.set_progress(task.id, phase_end)
        concat_list = _write_concat_list(normalized_paths, staging_dir)
        final_cmd = build_final_cmd(
            concat_list=concat_list,
            master_title=master_title,
            bgm_track=bgm_track,
            bgm_volume=settings_dict.get("bgm_volume", 0.4),
            total_duration=total_duration,
            out_path=final_path,
            staging_dir=staging_dir,
            aspect=aspect,
            master_title_style=master_style,
            ducking_threshold=settings_dict.get("ducking_threshold"),
            ducking_ratio=settings_dict.get("ducking_ratio"),
        )

        def on_progress(seconds_done):
            frac = (seconds_done / total_duration
                    if total_duration else 0.0)
            runner.set_progress(
                task.id,
                phase_end
                + (PHASE_ASSEMBLY_END - phase_end) * min(frac, 1.0),
            )

        encoder_used = encoder_svc.attempt_video_encode(
            final_cmd, on_progress=on_progress, total_duration=total_duration
        )
        job.encoder_used = encoder_used
        job.save(update_fields=["encoder_used"])

        # ---------------- phase 3: probe + finalize ----------------------
        runner.set_progress(task.id, PHASE_ASSEMBLY_END)
        facts = probe_media(final_path)
        out_w, out_h = filters_svc.out_dims(aspect)
        if facts["width"] != out_w or facts["height"] != out_h:
            raise RenderError(
                f"Output has unexpected dimensions "
                f"{facts['width']}x{facts['height']} "
                f"(expected {out_w}x{out_h})"
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
            "aspect": aspect,
        }
    finally:
        # Staging is always cleaned, success or failure.
        shutil.rmtree(staging_dir, ignore_errors=True)
        try:
            # Drop the .staging root too when this was its last tenant.
            staging_dir.parent.rmdir()
        except OSError:
            pass
