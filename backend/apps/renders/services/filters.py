"""FFmpeg filtergraph builders for the render pipeline.

Every per-clip normalization command is assembled here so the filtergraph
lives in exactly one place.  Output invariants (relied upon by the concat
demuxer in the final assembly step):

* exactly one 1080x1920, 30 fps, yuv420p, even-dimension video stream
* exactly one 48 kHz stereo AAC audio stream (real or injected silence)
"""
import os

from .textutil import escape_text, write_text_file

# Canonical shorts format.
OUT_W = 1080
OUT_H = 1920
OUT_FPS = 30
AUDIO_RATE = 48000

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

_FONT_CACHE = None


class FilterError(Exception):
    """Raised when the filtergraph cannot be built (e.g. no font found)."""


def resolve_font():
    """Return the path of the first available bold font.

    Prefers LiberationSans-Bold.ttf, falls back to DejaVuSans-Bold.ttf and
    raises a clear :class:`FilterError` when neither exists.
    """
    global _FONT_CACHE
    if _FONT_CACHE:
        return _FONT_CACHE
    for candidate in FONT_CANDIDATES:
        if os.path.isfile(candidate):
            _FONT_CACHE = candidate
            return candidate
    raise FilterError(
        "No usable font found for text overlays: tried "
        + ", ".join(FONT_CANDIDATES)
    )


def even(value):
    """Round *value* to the nearest even integer (h264 needs even dims)."""
    return max(2, int(round(value)) // 2 * 2)


def build_video_chain(video_height_pct, background_blur):
    """Build the 0:v filterchain up to and including the overlays.

    Returns ``(filter_complex, staging_paths)`` where *staging_paths* holds
    temp files (title textfiles) that must exist for the duration of the run.
    """
    font = resolve_font()
    height_pct = int(video_height_pct)
    fg_h = even(OUT_H * height_pct / 100.0)

    if background_blur:
        bg = (
            f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H},"
            "boxblur=luma_radius=20:luma_power=2"
        )
    else:
        bg = (
            f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H},"
            "eq=brightness=-0.12"
        )

    return bg, fg_h, font


def normalize_clip_cmd(
    src_path,
    start,
    end,
    rank,
    title,
    video_height_pct,
    background_blur,
    out_path,
    staging_dir,
):
    """Return the full argv list (excluding the ffmpeg binary) that renders
    the [start, end) window of *src_path* into a normalized clip at
    *out_path*.

    Uniformity contract: the output always has one 1080x1920 30fps yuv420p
    video stream and one 48 kHz stereo AAC stream, regardless of the source
    resolution, fps, or audio presence.
    """
    bg, fg_h, font = build_video_chain(video_height_pct, background_blur)
    duration = float(end) - float(start)
    title = (str(title) if title is not None else "").strip()

    parts = []
    # ---- background + foreground + overlay ----
    parts.append(
        f"[0:v]null[main];"
        f"[main]split=2[bgsrc][fgsrc];"
        f"[bgsrc]{bg}[bg];"
        f"[fgsrc]scale=-2:{fg_h},setsar=1[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={OUT_FPS},format=yuv420p[base]"
    )

    # ---- rank badge (top-left, always drawn) ----
    badge = f"#{int(rank)}"
    rank_drawtext = (
        f"drawtext=fontfile={font}:text='{escape_text(badge)}':fontsize=120"
        f":fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=28:x=48:y=40"
    )

    # ---- clip title (bottom-center, only when non-empty) ----
    staging = []
    title_drawtext = ""
    if title:
        truncated = title[:60]
        textfile = write_text_file(truncated, directory=staging_dir)
        staging.append(textfile)
        title_drawtext = (
            f",drawtext=fontfile={font}:textfile='{textfile}'"
            f":fontsize=62:box=1:boxcolor=black@0.55:boxborderw=22"
            f":x=(w-text_w)/2:y=h-190"
        )

    parts.append(f"[base]{rank_drawtext}{title_drawtext}[vout]")

    # ---- audio ----
    # atrim guards against tiny seek drift; loudnorm normalizes loudness;
    # aresample+aformat pin the stream to 48 kHz stereo.
    audio_chain = (
        f"atrim=duration={duration:.6f},asetpts=PTS-STARTPTS,"
        "loudnorm=I=-16:TP=-1.5:LRA=11,"
        f"aresample={AUDIO_RATE},aformat=channel_layouts=stereo"
    )

    # The main input may or may not carry audio.  We probe once per clip in
    # the pipeline (via the caller) — here we build both variants.
    return {
        "bg": bg,
        "fg_h": fg_h,
        "font": font,
        "duration": duration,
        "video_parts": parts,
        "audio_chain": audio_chain,
        "title_textfile": staging[0] if staging else None,
    }


def build_normalize_cmd(
    src_path,
    start,
    end,
    rank,
    title,
    video_height_pct,
    background_blur,
    has_audio,
    out_path,
    staging_dir,
):
    """Assemble the complete per-clip ffmpeg argv (without the binary).

    When the source has no audio an ``anullsrc`` lavfi input is injected and
    trimmed to the clip duration so every intermediate ends up with exactly
    one audio stream of the correct length.
    """
    info = normalize_clip_cmd(
        src_path, start, end, rank, title,
        video_height_pct, background_blur, out_path, staging_dir,
    )
    duration = info["duration"]

    if has_audio:
        filter_complex = (
            ";".join(info["video_parts"])
            + f";[0:a]{info['audio_chain']}[aout]"
        )
        cmd = [
            "-nostdin", "-hide_banner", "-y",
            "-ss", f"{float(start):.6f}", "-t", f"{duration:.6f}",
            "-i", str(src_path),
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "[aout]",
        ]
    else:
        # Trim the silence generator to exactly the clip duration so the
        # audio stream never outruns the video.
        filter_complex = (
            ";".join(info["video_parts"])
            + f";[1:a]atrim=duration={duration:.6f},asetpts=PTS-STARTPTS,"
            f"{info['audio_chain']}[aout]"
        )
        cmd = [
            "-nostdin", "-hide_banner", "-y",
            "-ss", f"{float(start):.6f}", "-t", f"{duration:.6f}",
            "-i", str(src_path),
            "-f", "lavfi", "-i", f"anullsrc=r={AUDIO_RATE}:cl=stereo",
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "[aout]",
        ]

    cmd += [
        # Intermediates are local: encode fast, quality stays high.
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-video_track_timescale", "90000",
        str(out_path),
    ]
    return cmd


def build_master_title_drawtext(master_title, staging_dir):
    """Return ``(drawtext_fragment, textfile_path_or_None)`` for the master
    title, or ``("", None)`` when the title is empty.  Uses textfile= to
    sidestep escaping entirely."""
    if not master_title:
        return "", None
    textfile = write_text_file(str(master_title), directory=staging_dir)
    frag = (
        f",drawtext=fontfile={resolve_font()}:textfile='{textfile}'"
        f":fontsize=84:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=24"
        f":x=(w-text_w)/2:y=110"
    )
    return frag, textfile
