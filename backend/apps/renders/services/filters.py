"""FFmpeg filtergraph builders for the render pipeline (v1.2).

Every per-clip normalization command is assembled here so the filtergraph
lives in exactly one place.  Output invariants (relied upon by the concat
demuxer in the final assembly step):

* exactly one WxH, 30 fps, yuv420p, even-dimension video stream — WxH is
  1080x1920 for 9:16 renders and 1920x1080 for 16:9 renders
* exactly one 48 kHz stereo AAC audio stream (real or injected silence)

v1.2:

* dual aspect via ``ASPECTS``; ``out_dims()`` returns the active canvas
* background: ``gblur`` with configurable sigma when ``background_blur`` is
  set, solid black canvas otherwise
* per-clip typography from the studio style JSON (font/size/colour/stroke/
  shadow/badge/position) with v1.1 defaults when no style is supplied
* pre-trimmed HD-section inputs (start=end=None) skip -ss/-t seeking
"""
import os

from . import textutil as textutil_mod
from .textutil import (
    coerce_style,
    escape_text,
    normalize_color,
    resolve_style_font,
    write_text_file,
)

# Canonical output formats.
ASPECTS = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
}
DEFAULT_ASPECT = "9:16"

# v1.1 canonical shorts format — kept as module constants for callers that
# still import them (tests, older callers): they describe the default aspect.
OUT_W = 1080
OUT_H = 1920
OUT_FPS = 30
AUDIO_RATE = 48000

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

DEFAULT_BLUR_SIGMA = 25.0

_FONT_CACHE = None


class FilterError(Exception):
    """Raised when the filtergraph cannot be built (e.g. no font found)."""


def resolve_font():
    """Return the path of the first available bold system font.

    Prefers LiberationSans-Bold.ttf, falls back to DejaVuSans-Bold.ttf and
    raises a clear :class:`FilterError` when neither exists.  (Style fonts
    are resolved separately through :func:`textutil.resolve_style_font`.)
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


def normalize_aspect(aspect):
    """Return a validated aspect key from '9:16' / '16:9' (default 9:16)."""
    return aspect if aspect in ASPECTS else DEFAULT_ASPECT


def out_dims(aspect):
    """Return (width, height) for *aspect*."""
    return ASPECTS[normalize_aspect(aspect)]


def even(value):
    """Round *value* to the nearest even integer (h264 needs even dims)."""
    return max(2, int(round(value)) // 2 * 2)


# ---------------------------------------------------------------------------
# Positioning
# ---------------------------------------------------------------------------

def _title_y_expr(style, offset=0):
    """drawtext ``y=`` expression for a textPosition preset.

    *offset* shifts the line down by a fixed pixel amount (used to place
    the subtitle below the title).
    """
    position = style.get("textPosition") or "bottom"
    margin = style.get("margin")
    suffix = f"+{int(offset)}" if offset else ""
    if position == "top":
        base = f"{int(margin)}" if margin else "h*0.04"
        return f"{base}{suffix}"
    if position == "center":
        return f"(h-text_h)/2{suffix}"
    # bottom: title box top sits `margin` (default 10% of height) above the
    # bottom edge; offset moves it further up for the subtitle.
    base = f"h-text_h-{int(margin)}" if margin else "h-text_h-h*0.10"
    return f"{base}{suffix}"


def _layout_for_rank(aspect):
    """Rank badge layout constants per aspect (v1.1 defaults)."""
    if aspect == "16:9":
        return {"rank_fontsize": 96, "rank_x": 48, "rank_y": 40,
                "rank_boxborderw": 24}
    return {"rank_fontsize": 120, "rank_x": 48, "rank_y": 40,
            "rank_boxborderw": 28}


# ---------------------------------------------------------------------------
# Drawtext fragment builders
# ---------------------------------------------------------------------------

def _style_font(style):
    """Style font if resolvable, else the system fallback."""
    return resolve_style_font(style.get("font")) or resolve_font()


def build_style_drawtext(
    text, style, aspect, staging_dir, prefix="vrs_title_",
    use_textfile=None, y_expr=None,
):
    """Build one drawtext fragment from a resolved style dict.

    Returns ``(fragment, textfile_path_or_None)``.  Risky text always goes
    through ``textfile=``; short simple strings stay inline (``text=``)
    unless *use_textfile* forces the file route.  *y_expr* overrides the
    position-derived y expression (used for subtitle placement).

    Style keys consumed: font, fontSize, textColor, strokeColor, strokeWidth,
    shadow (bool or {color,x,y}), badgeBg, badgeOpacity, textPosition, margin.
    """
    style = coerce_style(style)
    opts = [
        f"fontfile={_style_font(style)}",
        f"fontsize={int(style['fontSize'])}",
        f"fontcolor={normalize_color(style.get('textColor'), 'white')}",
    ]

    stroke_w = int(style.get("strokeWidth") or 0)
    if stroke_w > 0:
        opts.append(f"borderw={stroke_w}")
        opts.append(
            f"bordercolor={normalize_color(style.get('strokeColor'), 'black')}"
        )

    shadow = style.get("shadow")
    if shadow:
        if isinstance(shadow, dict):
            sx = int(float(shadow.get("x", 3)))
            sy = int(float(shadow.get("y", 3)))
            sc = normalize_color(shadow.get("color", "black"), "black")
        else:
            sx, sy, sc = 3, 3, "black"
        opts += [f"shadowcolor={sc}", f"shadowx={sx}", f"shadowy={sy}"]

    if style.get("badgeBg"):
        boxcolor = (
            f"{normalize_color(style['badgeBg'], 'black')}"
            f"@{float(style.get('badgeOpacity', 0.55)):.2f}"
        )
        opts += ["box=1", f"boxcolor={boxcolor}", "boxborderw=12"]

    opts.append("x=(w-text_w)/2")
    opts.append(f"y={y_expr if y_expr is not None else _title_y_expr(style)}")

    text = "" if text is None else str(text)
    textfile = None
    if use_textfile or len(text) > 40 or any(c in text for c in ":'\\%"):
        textfile = write_text_file(text, directory=staging_dir, prefix=prefix)
        opts.append(f"textfile='{textfile}'")
    else:
        opts.append(f"text='{escape_text(text)}'")

    return "drawtext=" + ":".join(opts), textfile


def build_subtitle_drawtext(subtitle, style, aspect, staging_dir):
    """Drawtext fragment for the optional per-clip subtitle line.

    Sits one title-line below the main title (or below the top margin),
    at ~62% of the title size, same font/colour family, own subtle badge.
    Returns ``(fragment_or_empty, textfile_or_None)``.
    """
    if not subtitle:
        return "", None
    title_style = coerce_style(style)
    sub_style = dict(title_style)
    title_fs = int(title_style["fontSize"] or 62)
    sub_style["fontSize"] = max(12, int(title_fs * 0.62))
    gap = int(title_fs * 1.55)
    y_expr = _title_y_expr(title_style, offset=gap)
    return build_style_drawtext(
        str(subtitle)[:80], sub_style, aspect, staging_dir,
        prefix="vrs_sub_", use_textfile=True, y_expr=y_expr,
    )


# ---------------------------------------------------------------------------
# Video chain
# ---------------------------------------------------------------------------

def build_bg_chain(aspect, background_blur, blur_sigma):
    """Filterchain producing the blurred background canvas from a frame.

    blurred: scale-cover the frame to the canvas then ``gblur`` with the
    requested sigma (default 25).  The unblurred variant is handled by the
    caller with a solid black ``color`` source instead.
    """
    w, h = out_dims(aspect)
    try:
        sigma = float(blur_sigma)
    except (TypeError, ValueError):
        sigma = DEFAULT_BLUR_SIGMA
    if sigma <= 0:
        sigma = DEFAULT_BLUR_SIGMA
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},"
        f"gblur=sigma={sigma:.2f}"
    )


def build_video_chain(video_height_pct, background_blur, aspect=DEFAULT_ASPECT,
                      blur_sigma=DEFAULT_BLUR_SIGMA):
    """Build the 0:v filterchain parameters up to and including the overlays.

    Returns ``(bg_chain, fg_h, font)``.  For 16:9 the foreground height
    percentage is interpreted against the 1080p canvas (contain-fit when
    below 100%).  ``bg_chain`` is empty when the background is a solid
    black canvas rather than a blurred cover.
    """
    font = resolve_font()
    w, h = out_dims(aspect)
    height_pct = int(video_height_pct)

    if background_blur:
        bg = build_bg_chain(aspect, background_blur, blur_sigma)
    else:
        bg = ""  # solid black canvas built by the caller
    fg_h = even(h * height_pct / 100.0)
    return bg, fg_h, font


def normalize_clip_cmd(
    src_path,
    start,
    end,
    rank,
    title,
    video_height_pct,
    background_blur,
    aspect=DEFAULT_ASPECT,
    blur_sigma=DEFAULT_BLUR_SIGMA,
    style=None,
    subtitle=None,
    volume=None,
    duration_hint=None,
    out_path=None,
    staging_dir=None,
):
    """Return the partial command description for one normalized clip.

    Uniformity contract: the output always has one WxH 30fps yuv420p video
    stream and one 48 kHz stereo AAC stream, regardless of the source
    resolution, fps, or audio presence.

    ``start=end=None`` marks a pre-trimmed HD-section input: no -ss/-t
    seeking, and *duration_hint* (probed upstream) drives the black canvas
    duration and the audio trim guard.

    Returns a dict with video_parts, audio_chain, duration, title_textfile.
    """
    hd_input = start is None or end is None
    if hd_input:
        duration = float(duration_hint or 0.0)
    else:
        duration = float(end) - float(start)

    bg, fg_h, font = build_video_chain(
        video_height_pct, background_blur, aspect=aspect, blur_sigma=blur_sigma
    )
    title = (str(title) if title is not None else "").strip()
    dims = out_dims(aspect)
    layout = _layout_for_rank(aspect)
    style = coerce_style(style)

    parts = []
    # ---- background + foreground + overlay ----
    if bg:
        parts.append(
            f"[0:v]null[main];"
            f"[main]split=2[bgsrc][fgsrc];"
            f"[bgsrc]{bg}[bg];"
            f"[fgsrc]scale=-2:{fg_h},setsar=1[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={OUT_FPS},format=yuv420p[base]"
        )
    else:
        # Solid black canvas; d is generous and shortest=1 ends the overlay
        # at the (possibly input-trimmed) foreground's end.
        parts.append(
            f"color=c=black:s={dims[0]}x{dims[1]}:r={OUT_FPS}"
            f":d={duration + 1.0:.6f}[bg];"
            f"[0:v]scale=-2:{fg_h},setsar=1[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1,fps={OUT_FPS},"
            f"format=yuv420p[base]"
        )

    # ---- rank badge (top-left, always drawn) ----
    badge = f"#{int(rank)}"
    rank_drawtext = (
        f"drawtext=fontfile={_style_font(style)}"
        f":text='{escape_text(badge)}'"
        f":fontsize={layout['rank_fontsize']}"
        f":fontcolor=white:box=1:boxcolor=black@0.55"
        f":boxborderw={layout['rank_boxborderw']}"
        f":x={layout['rank_x']}:y={layout['rank_y']}"
    )

    # ---- clip title (styled, per-clip) ----
    staging = []
    drawtexts = [rank_drawtext]
    if title:
        title_frag, textfile = build_style_drawtext(
            title[:60], style, aspect, staging_dir, use_textfile=True,
        )
        if textfile:
            staging.append(textfile)
        drawtexts.append(title_frag)

    # ---- optional subtitle ----
    if subtitle:
        sub_frag, sub_file = build_subtitle_drawtext(
            subtitle, style, aspect, staging_dir
        )
        if sub_frag:
            drawtexts.append(sub_frag)
            if sub_file:
                staging.append(sub_file)

    parts.append("[base]" + ",".join(drawtexts) + "[vout]")

    # ---- audio ----
    # atrim guards against tiny seek drift; loudnorm normalizes loudness;
    # aresample+aformat pin the stream to 48 kHz stereo.  Per-clip volume
    # (v1.2) is applied before loudnorm so normalization still levels the
    # *relative* mix downstream... loudnorm would undo a volume change, so
    # apply per-clip volume AFTER loudnorm instead.
    volume_frag = ""
    if volume is not None:
        try:
            vol = float(volume)
        except (TypeError, ValueError):
            vol = None
        if vol is not None and abs(vol - 1.0) > 1e-6:
            volume_frag = f"volume={vol:.4f},"

    audio_chain = (
        f"atrim=duration={duration:.6f},asetpts=PTS-STARTPTS,"
        "loudnorm=I=-16:TP=-1.5:LRA=11,"
        f"{volume_frag}"
        f"aresample={AUDIO_RATE},aformat=channel_layouts=stereo"
    )

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
    aspect=DEFAULT_ASPECT,
    blur_sigma=DEFAULT_BLUR_SIGMA,
    style=None,
    subtitle=None,
    volume=None,
    duration_hint=None,
):
    """Assemble the complete per-clip ffmpeg argv (without the binary).

    When the source has no audio an ``anullsrc`` lavfi input is injected and
    trimmed to the clip duration so every intermediate ends up with exactly
    one audio stream of the correct length.

    ``start=end=None`` selects the pre-trimmed HD-section mode: no -ss/-t
    and *duration_hint* (the probed natural duration) drives trim guards.
    """
    hd_input = start is None or end is None
    if hd_input:
        start_f, end_f = None, None
    else:
        start_f, end_f = float(start), float(end)

    info = normalize_clip_cmd(
        src_path, start_f, end_f, rank, title,
        video_height_pct, background_blur,
        aspect=aspect, blur_sigma=blur_sigma, style=style,
        subtitle=subtitle, volume=volume, duration_hint=duration_hint,
    )

    if hd_input:
        # Pre-trimmed HD section: bound the run to the probed natural
        # duration plus a small guard so v/a drift stays bounded.
        input_flags = ["-t", f"{(info['duration'] or 0.0) + 0.25:.6f}"]
    else:
        input_flags = ["-ss", f"{start_f:.6f}", "-t",
                       f"{info['duration']:.6f}"]
    if has_audio:
        filter_complex = (
            ";".join(info["video_parts"])
            + f";[0:a]{info['audio_chain']}[aout]"
        )
        cmd = (
            ["-nostdin", "-hide_banner", "-y"] + input_flags
            + ["-i", str(src_path),
               "-filter_complex", filter_complex,
               "-map", "[vout]", "-map", "[aout]"]
        )
    else:
        filter_complex = (
            ";".join(info["video_parts"])
            + f";[1:a]atrim=duration={info['duration']:.6f},"
              "asetpts=PTS-STARTPTS,"
            + info["audio_chain"] + "[aout]"
        )
        cmd = (
            ["-nostdin", "-hide_banner", "-y"] + input_flags
            + ["-i", str(src_path),
               "-f", "lavfi", "-i", f"anullsrc=r={AUDIO_RATE}:cl=stereo",
               "-filter_complex", filter_complex,
               "-map", "[vout]", "-map", "[aout]"]
        )

    cmd += [
        # Intermediates are local: encode fast, quality stays high.
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-video_track_timescale", "90000",
        str(out_path),
    ]
    return cmd


def build_master_title_drawtext(master_title, staging_dir,
                                aspect=DEFAULT_ASPECT, style=None):
    """Return ``(drawtext_fragment, textfile_path_or_None)`` for the master
    title, or ``("", None)`` when the title is empty.  Uses textfile= to
    sidestep escaping entirely.

    v1.2: *style* (master_title_style) supports the same keys as clip
    styles; defaults reproduce the v1.1 look (white 84px, black badge,
    centered, 110px from the top edge).
    """
    if not master_title:
        return "", None
    # Master-specific defaults (v1.1 look), overridden by any supplied style.
    base = dict(textutil_mod.DEFAULT_TEXT_STYLE)
    base["fontSize"] = 84
    merged = coerce_style(style, base=base)
    # The master title is pinned near the top edge of the frame.
    merged["textPosition"] = "top"
    if merged.get("margin") is None:
        merged["margin"] = 60 if normalize_aspect(aspect) == "16:9" else 110
    frag, textfile = build_style_drawtext(
        master_title, merged, aspect, staging_dir,
        prefix="vrs_master_", use_textfile=True,
    )
    return "," + frag, textfile
