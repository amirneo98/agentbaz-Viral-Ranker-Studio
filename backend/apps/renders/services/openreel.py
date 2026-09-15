"""OpenReel → VRS render-pipeline converter + animation preset mapping (v1.3).

Maps an OpenReel ProjectFile JSON (schema v1.2.0 — see
third_party/openreel-video/packages/core/src/storage/project-serializer.ts
and types/project.ts, read-only reference) onto the v1.2 render pipeline
payload consumed by ``apps.renders.services.pipeline.run_render``.

ProjectFile subset the converter consumes:

  {"version": "1.2.0", "project": {
     "id", "name",
     "settings": {width, height, frameRate, ...},
     "mediaLibrary": {"items": [
        {"id", "type": "video"|"image", "originalUrl":
           "http://host:8000/media/videos/x.mp4"        # our full quality
         | "http://host:8000/media/previews/<uuid>.mp4" # 720p proxy
         | any remote http(s) URL,
         "metadata": {duration, width, height, ...}}]},
     "timeline": {"tracks": [
        {"id", "type": "video"|..., "hidden": false, "clips": [
           {"id", "mediaId", "startTime", "duration",
            "inPoint", "outPoint", "volume"}]}],
       "duration": ...},
     "textClips": [
        {"id", "startTime", "duration", "text",
         "style": {fontFamily, fontSize, color, strokeColor, strokeWidth,
                   shadowColor, shadowBlur, shadowOffsetX, shadowOffsetY,
                   textAlign, verticalAlign, lineHeight},
         "transform": {position: {x, y}, scale, rotation, opacity},
         "animation": {preset, inDuration, outDuration, params}}]}}

The caller also supplies a *ranking* block:

  {"clips": [{"mediaId", "rank", "title", "start", "end",
              "textAnim", "textStyle"}],
   "master_title": str, "aspect": "9:16"|"16:9"}

Media resolution per timeline clip (by mediaId):

1. originalUrl → our ``media/videos`` or ``media/hd_clips``: use the file
   directly (Video row when it matches, else the raw path) with trim.
2. originalUrl → ``media/previews/<uuid>.mp4`` proxy: find the studio Clip
   with that pk → use its ``hd_file`` when ready, else pass ``source_url``
   so the pipeline performs the full YtDlpDownloader download (identical
   fallback to coerce_v12_payload today).
3. anything else → ``source_url`` passthrough (render-time download).

Text overlays become drawtext filtergraphs with time expressions; the 26
presets map onto the verified-safe ffmpeg 8.0.1 expression set (NOTE:
time-dependent ``fontsize=`` expressions SEGFAULT this ffmpeg build, so
scale-family presets are approximated with position/alpha motion):
``PRESET_MAPPINGS`` documents every mapping. Persian/RTL works through
apps.renders.services.textutil (fribidi shaping) unchanged.
"""
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.conf import settings

from . import textutil

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class OpenReelConversionError(Exception):
    """Fatal conversion problem surfaced to the API as a 400."""


# ---------------------------------------------------------------------------
# URL classification
# ---------------------------------------------------------------------------

#: MEDIA_ROOT subpaths holding full-quality VRS media.
_FULL_QUALITY_SEGMENTS = ("/videos/", "/hd_clips/")
#: MEDIA_ROOT subpath holding 720p preview proxies.
_PREVIEW_SEGMENT = "/previews/"


def _path_of(url):
    """Unquoted path component of *url* ("" when unusable)."""
    try:
        return unquote(urlparse(str(url)).path)
    except ValueError:
        return ""


def classify_media_url(url):
    """Classify an originalUrl.

    Returns one of:

    * ``("full", media-relative-path)``    — media/videos | media/hd_clips
    * ``("preview", media-relative-path)`` — media/previews (720p proxy)
    * ``("media", media-relative-path)``   — other local /media/ file
    * ``("remote", url)``                  — any other http(s) URL
    * ``(None, None)``                     — unusable
    """
    if not url or not isinstance(url, str):
        return None, None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None, None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None, None
    path = unquote(parsed.path)
    if not path.startswith("/media/"):
        return "remote", url
    rel = path[len("/media/"):].lstrip("/")
    if not rel:
        return None, None
    for seg in _FULL_QUALITY_SEGMENTS:
        if rel.startswith(seg.strip("/")):
            return "full", rel
    if rel.startswith(_PREVIEW_SEGMENT.strip("/")):
        return "preview", rel
    return "media", rel


def _media_path(rel):
    """Absolute path for a media-relative path (no existence check)."""
    return Path(settings.MEDIA_ROOT) / rel


# ---------------------------------------------------------------------------
# ProjectFile validation
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1.2.0"


def _version_tuple(value):
    out = []
    for part in str(value or "").split(".")[:3]:
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


def validate_project_file(project_file):
    """Validate the minimal ProjectFile structure the converter needs.

    Returns the inner project dict; raises OpenReelConversionError with a
    field-addressed message otherwise (mirrors the TS validateProjectJson
    checks for the fields we consume).
    """
    if not isinstance(project_file, dict):
        raise OpenReelConversionError("project must be a JSON object")
    version = project_file.get("version")
    if not version:
        raise OpenReelConversionError("project file: missing version field")
    min_reader = project_file.get("minimumReaderVersion")
    if min_reader and _version_tuple(min_reader) > _version_tuple(SCHEMA_VERSION):
        raise OpenReelConversionError(
            f"project requires OpenReel reader {min_reader} or newer "
            f"(backend supports {SCHEMA_VERSION})"
        )
    project = project_file.get("project")
    if not isinstance(project, dict):
        raise OpenReelConversionError("project file: missing project field")
    for field in ("id", "name"):
        if not project.get(field):
            raise OpenReelConversionError(f"project.{field} is required")
    if not isinstance(project.get("settings"), dict):
        raise OpenReelConversionError("project.settings is required")
    timeline = project.get("timeline")
    if not isinstance(timeline, dict):
        raise OpenReelConversionError("project.timeline is required")
    if not isinstance(timeline.get("tracks"), list):
        raise OpenReelConversionError("project.timeline.tracks must be a list")
    library = project.get("mediaLibrary")
    if (not isinstance(library, dict)
            or not isinstance(library.get("items"), list)):
        raise OpenReelConversionError("project.mediaLibrary.items is required")

    media_ids = set()
    for item in library["items"]:
        if not isinstance(item, dict) or not item.get("id"):
            raise OpenReelConversionError(
                "project.mediaLibrary.items: every item needs an id"
            )
        media_ids.add(item["id"])

    for track in timeline["tracks"]:
        if not isinstance(track, dict):
            raise OpenReelConversionError("project.timeline.tracks: invalid track")
        for clip in track.get("clips") or []:
            if not isinstance(clip, dict):
                raise OpenReelConversionError("invalid clip in timeline track")
            media_id = clip.get("mediaId")
            if not media_id:
                continue
            if str(media_id).startswith(
                ("text-", "shape-", "svg-", "sticker-", "motion-")
            ):
                continue  # virtual clip — not a media reference
            if media_id not in media_ids:
                raise OpenReelConversionError(
                    f"clip {clip.get('id') or '?'} references non-existent "
                    f"mediaId: {media_id}"
                )
    return project


# ---------------------------------------------------------------------------
# Animation preset → ffmpeg mapping (26 presets)
# ---------------------------------------------------------------------------

#: Every preset name OpenReel can emit (text/types.ts TextAnimationPreset).
ALL_PRESETS = (
    "none", "typewriter", "fade", "slide-left", "slide-right", "slide-up",
    "slide-down", "scale", "blur", "bounce", "rotate", "wave", "shake",
    "pop", "glitch", "split", "flip", "word-by-word", "rainbow", "rise",
    "drop", "elastic", "swing", "zoom-blur", "cascade",
)

#: Documented mapping table: preset → ffmpeg technique (verifiable — every
#: technique below was smoke-tested against this exact ffmpeg 8.0.1 build;
#: time-dependent fontsize expressions segfault it and are NOT used).
PRESET_MAPPINGS = {
    "none":         "static drawtext, no time expressions",
    "typewriter":   "per-word drawtext with staggered enable='between(t,i*step,end)' "
                    "windows (character-granular windows are quadratic; word "
                    "granularity approximates the reveal)",
    "fade":         "alpha='if(lt(t,t0+IN),max(0,(t-t0)/IN),"
                    "if(gt(t,END-OUT),max(0,(END-t)/OUT),1))'",
    "slide-left":   "x = base - DIST*(1-easeOut(t-t0)/IN)",
    "slide-right":  "x = base + DIST*(1-easeOut(...))",
    "slide-up":     "y = base + DIST*(1-easeOut(...))  (rises from below)",
    "slide-down":   "y = base - DIST*(1-easeOut(...))  (drops from above)",
    "scale":        "fontsize expressions segfault ffmpeg 8.0.1 → approximated "
                    "as slide-up + fade (scaleFrom/scaleTo folded into the "
                    "slide distance)",
    "blur":         "drawtext has no per-frame blur → approximated as fade",
    "bounce":       "y = base - H*abs(sin(PI*t/T))*exp(-3*progress)",
    "rotate":       "rotation is not expressible inside drawtext → approximated "
                    "as wave (vertical oscillation)",
    "wave":         "y = base + A*sin(2*PI*f*t)",
    "shake":        "x = base + I*sin(S*t), y = base + I*cos(1.7*S*t)",
    "pop":          "scale overshoot approximated: bounce-style y punch + fade-in "
                    "(fontsize overshoot would segfault)",
    "glitch":       "x = base + I*sin(S*t*t), alpha *= (0.75+0.25*sin(7*S*t))",
    "split":        "approximated as slide-left (per-half split needs two "
                    "halves of the rasterized text, not expressible)",
    "flip":         "mirror flip not expressible → approximated as slide-right "
                    "+ fade",
    "word-by-word": "per-word drawtext with enable='between(t,i*delay,end)' "
                    "windows, each with a fast fade",
    "rainbow":      "per-frame hue rotation not expressible in fontcolor → "
                    "best-effort alpha shimmer (0.8+0.2*sin(2*PI*speed*t))",
    "rise":         "y slide-up + alpha fade-in",
    "drop":         "y slide-down + alpha fade-in",
    "elastic":      "scale elastic approximated: wave-style y oscillation with "
                    "exp() decay + fade-in",
    "swing":        "x = base + A*sin(2*PI*f*t)*exp(-2*t/DUR)",
    "zoom-blur":    "approximated as slide-up (slight) + fade-in",
    "cascade":      "per-word staggered windows (like word-by-word) with "
                    "rise-style slide per word via staggered starts",
}

_DEFAULT_SLIDE_PX = 120.0
_DEFAULT_FADE_S = 0.4


def _num(source, key, default):
    """Numeric field with NaN guard; *default* when missing/invalid."""
    try:
        value = float(source.get(key))
    except (TypeError, ValueError, AttributeError):
        return default
    return value if value == value else default


def _clamp(value, lo, hi):
    return lo if value < lo else hi if value > hi else value


def _ease_out(t0, dur):
    """easeOutCubic progress expression over [t0, t0+dur]."""
    p = f"min(max((t-{t0:.3f})/{max(dur, 0.01):.3f},0),1)"
    return f"(1-pow(1-{p},3))"


def _fade_alpha(t0, total, in_dur, out_dur, alpha_base):
    """alpha expression: linear in, hold, linear out."""
    t_end = t0 + total
    out_dur = max(out_dur, 0.01)
    return (
        f"{alpha_base:.4f}*if(lt(t,{t0 + in_dur:.3f}),"
        f"max(0,(t-{t0:.3f})/{max(in_dur, 0.01):.3f}),"
        f"if(gt(t,{t_end - out_dur:.3f}),"
        f"max(0,({t_end:.3f}-t)/{out_dur:.3f}),1))"
    )


def _ramp_alpha(t0, in_dur, alpha_base):
    """alpha expression: linear fade-in, no fade-out."""
    return (
        f"{alpha_base:.4f}*min(max((t-{t0:.3f})/{max(in_dur, 0.01):.3f},0),1)"
    )


def _build_static_or_animated(text, style, transform, animation, staging_dir,
                              clip_offset, total, prefix, t0=None):
    """Core single-drawtext builder (all presets except the per-word ones).

    Returns ``(fragment, textfile_or_None)``.
    """
    style = style if isinstance(style, dict) else {}
    transform = transform if isinstance(transform, dict) else {}
    animation = animation if isinstance(animation, dict) else {}
    params = animation.get("params") if isinstance(
        animation.get("params"), dict) else {}

    preset = animation.get("preset") or "none"
    in_dur = _clamp(_num(animation, "inDuration", _DEFAULT_FADE_S), 0.01, 5.0)
    out_dur = _clamp(_num(animation, "outDuration", 0.3), 0.0, 5.0)
    if t0 is None:
        t0 = max(0.0, float(clip_offset or 0.0))

    # ---- styling ------------------------------------------------------
    font = textutil.resolve_style_font(style.get("fontFamily"))
    if font is None:
        from . import filters as filters_svc

        font = filters_svc.resolve_font()
    font_size = int(_clamp(_num(style, "fontSize", 48), 8, 400))

    opts = [
        f"fontfile={font}",
        f"fontsize={font_size}",
        f"fontcolor={textutil.normalize_color(style.get('color'), 'white')}",
    ]
    stroke_w = int(_clamp(_num(style, "strokeWidth", 0), 0, 20))
    if stroke_w > 0:
        opts += [
            f"borderw={stroke_w}",
            "bordercolor="
            + textutil.normalize_color(style.get("strokeColor"), "black"),
        ]
    if style.get("shadowColor"):
        opts += [
            "shadowcolor="
            + textutil.normalize_color(style["shadowColor"], "black"),
            f"shadowx={int(_num(style, 'shadowOffsetX', 3))}",
            f"shadowy={int(_num(style, 'shadowOffsetY', 3))}",
        ]

    # ---- base position (normalized transform 0..1) --------------------
    pos = transform.get("position") if isinstance(
        transform.get("position"), dict) else {}
    base_x = f"(w-text_w)*{_clamp(_num(pos, 'x', 0.5), 0.0, 1.0):.4f}"
    base_y = f"(h-text_h)*{_clamp(_num(pos, 'y', 0.5), 0.0, 1.0):.4f}"
    v_align = style.get("verticalAlign")
    if v_align == "top":
        base_y = "h*0.08"
    elif v_align == "bottom":
        base_y = "h-text_h-h*0.08"

    alpha_base = _clamp(_num(transform, "opacity", 1.0), 0.0, 1.0)

    # ---- preset expressions -------------------------------------------
    x_expr, y_expr = base_x, base_y
    alpha_expr = f"{alpha_base:.4f}"

    slide_px = _num(params, "slideDistance", _DEFAULT_SLIDE_PX)
    ease = _ease_out(t0, in_dur)
    fade_in_alpha = _ramp_alpha(t0, in_dur, alpha_base)
    full_fade_alpha = _fade_alpha(t0, total, in_dur, out_dur, alpha_base)

    if preset == "none":
        pass
    elif preset == "fade":
        alpha_expr = full_fade_alpha
    elif preset in ("slide-left", "split"):
        x_expr = f"{base_x}-{slide_px:.1f}*(1-{ease})"
    elif preset == "slide-right":
        x_expr = f"{base_x}+{slide_px:.1f}*(1-{ease})"
    elif preset in ("slide-up", "rise", "zoom-blur"):
        y_expr = f"{base_y}+{slide_px:.1f}*(1-{ease})"
        alpha_expr = fade_in_alpha
    elif preset in ("slide-down", "drop"):
        y_expr = f"{base_y}-{slide_px:.1f}*(1-{ease})"
        alpha_expr = fade_in_alpha
    elif preset in ("scale", "flip", "blur", "rotate"):
        # fontsize time-expressions segfault ffmpeg 8.0.1 → motion stand-in.
        scale_from = _num(params, "scaleFrom", 0.5)
        scale_to = _num(params, "scaleTo", 1.0)
        span = _DEFAULT_SLIDE_PX * _clamp(scale_to - scale_from, 0.1, 2.0)
        if preset in ("scale", "blur", "flip"):
            y_expr = f"{base_y}+{span:.1f}*(1-{ease})"
        else:  # rotate → vertical oscillation
            y_expr = f"{base_y}+24*sin(2*3.14159*1.5*t)*{ease}"
        alpha_expr = fade_in_alpha
    elif preset == "pop":
        # overshoot via a quick bounce punch upward + fade-in
        p = f"min(max((t-{t0:.3f})/{in_dur:.3f},0),1)"
        y_expr = f"{base_y}-40*abs(sin(3.14159*{p}))*exp(-2*{p})"
        alpha_expr = fade_in_alpha
    elif preset == "elastic":
        amp = _num(params, "popOvershoot", 0.25)
        p = f"min(max((t-{t0:.3f})/{in_dur:.3f},0),1)"
        y_expr = (
            f"{base_y}-{_DEFAULT_SLIDE_PX:.1f}*{amp:.3f}"
            f"*abs(sin(12*{p}))*exp(-6*{p})"
        )
        alpha_expr = fade_in_alpha
    elif preset == "bounce":
        height = _num(params, "bounceHeight", 60)
        p = f"min(max((t-{t0:.3f})/{in_dur:.3f},0),1)"
        y_expr = (
            f"{base_y}-{height:.1f}*abs(sin(3.14159*t/0.5))*exp(-3*{p})"
        )
    elif preset == "wave":
        amplitude = _num(params, "waveAmplitude", 24)
        frequency = _num(params, "waveFrequency", 2.0)
        y_expr = f"{base_y}+{amplitude:.1f}*sin(2*3.14159*{frequency:.3f}*t)"
    elif preset == "shake":
        intensity = _num(params, "shakeIntensity", 12)
        speed = _num(params, "shakeSpeed", 8.0)
        x_expr = f"{base_x}+{intensity:.1f}*sin({speed:.2f}*t)"
        y_expr = f"{base_y}+{intensity:.1f}*cos({speed * 1.7:.2f}*t)"
    elif preset == "glitch":
        intensity = _num(params, "glitchIntensity", 10)
        speed = _num(params, "glitchSpeed", 12.0)
        x_expr = f"{base_x}+{intensity:.1f}*sin({speed:.2f}*t*t)"
        alpha_expr = f"{alpha_base:.4f}*(0.75+0.25*sin({speed * 7:.2f}*t))"
    elif preset == "swing":
        amplitude = _num(params, "waveAmplitude", 40)
        frequency = _num(params, "waveFrequency", 1.2)
        y_expr = (
            f"{base_y}+{amplitude:.1f}*sin(2*3.14159*{frequency:.3f}*t)"
            f"*exp(-2*t/{total:.3f})"
        )
    elif preset == "rainbow":
        speed = _num(params, "rainbowSpeed", 1.5)
        alpha_expr = (
            f"{alpha_base:.4f}*(0.8+0.2*sin(2*3.14159*{speed:.3f}*t))"
        )
    else:
        # Unknown preset: static drawtext (forward compatibility).
        pass

    opts.append(f"x='{x_expr}'")
    opts.append(f"y='{y_expr}'")
    opts.append(
        f"enable='between(t,{t0:.3f},{t0 + total:.3f})'"
    )
    opts.append(f"alpha='{alpha_expr}'")

    body, textfile = _text_option(text, staging_dir, prefix)
    return "drawtext=" + ":".join(opts + [body]), textfile


def _text_option(text, staging_dir, prefix):
    """(text/textfile option, textfile path or None). Always textfile for
    safety (Persian, colons, quotes)."""
    text = "" if text is None else str(text)
    if not text:
        return "text=''", None
    textfile = textutil.write_text_file(
        text, directory=staging_dir, prefix=prefix
    )
    return f"textfile='{textfile}'", textfile


def build_animated_drawtext(text, style, transform, animation, aspect,
                            staging_dir, clip_offset=0.0, clip_duration=None,
                            prefix="or_txt_"):
    """Build the drawtext fragment(s) for one OpenReel text overlay.

    Returns ``(fragment, textfile_or_None)``; *fragment* may contain
    several comma-joined drawtext filters for the per-word presets.

    *clip_offset* is the overlay's start relative to the owning clip's
    render (the per-clip normalization run starts at t=0); text shaping
    for Persian happens inside textutil.write_text_file (fribidi).
    """
    animation = animation if isinstance(animation, dict) else {}
    preset = animation.get("preset") or "none"
    total = clip_duration if clip_duration else 3.0
    total = max(0.1, float(total))

    if preset in ("typewriter", "word-by-word", "cascade"):
        return _build_word_windows(
            text, style, transform, animation, staging_dir,
            clip_offset, total, prefix,
        )
    return _build_static_or_animated(
        text, style, transform, animation, staging_dir,
        clip_offset, total, prefix,
    )


def _build_word_windows(text, style, transform, animation, staging_dir,
                        clip_offset, total, prefix):
    """typewriter / word-by-word / cascade: one drawtext per word with a
    staggered enable window; cascade words also slide up."""
    words = [w for w in re.split(r"\s+", str(text or "")) if w]
    if not words:
        return "", None
    params = animation.get("params") if isinstance(
        animation.get("params"), dict) else {}
    preset = animation.get("preset")
    in_dur = _clamp(_num(animation, "inDuration", _DEFAULT_FADE_S), 0.05, 5.0)

    if preset == "typewriter":
        # reveal the full text across the in-duration, word granularity
        step = max(0.02, in_dur / max(1, len(words)))
    elif preset == "cascade":
        step = max(0.05, _num(params, "wordDelay", 0.2))
    else:  # word-by-word
        step = max(0.05, _num(params, "wordDelay", 0.25))

    end = clip_offset + total
    frags = []
    for i, word in enumerate(words):
        start = clip_offset + i * step
        if preset == "cascade":
            anim_i = {
                "preset": "rise",
                "inDuration": max(0.05, step),
                "outDuration": 0.0,
                "params": {"slideDistance": 60},
            }
            frag, _ = _build_static_or_animated(
                word, style, transform, anim_i, staging_dir,
                clip_offset, total - i * step, f"{prefix}{i}_",
                t0=start,
            )
        else:
            anim_i = {
                "preset": "fade",
                "inDuration": min(0.15, step),
                "outDuration": 0.0,
                "params": {},
            }
            frag, _ = _build_static_or_animated(
                word, style, transform, anim_i, staging_dir,
                clip_offset, total - i * step, f"{prefix}{i}_",
                t0=start,
            )
        # pin the precise enable window
        frag = re.sub(
            r"enable='between\(t,[^)]*\)'",
            f"enable='between(t,{start:.3f},{end:.3f})'",
            frag,
        )
        frags.append(frag)
    return ",".join(frags), None


# ---------------------------------------------------------------------------
# Media resolution
# ---------------------------------------------------------------------------

_PREVIEW_NAME_RE = re.compile(r"^previews/([0-9a-fA-F-]{8,36})\.mp4$")


def _find_clip_by_proxy(rel_path):
    """Studio Clip owning a preview proxy, or None.

    The proxy service names files ``previews/<uuid>.mp4`` where <uuid> is
    the studio Clip pk (apps.renders.services.proxy convention).
    """
    from apps.studio.models import Clip

    match = _PREVIEW_NAME_RE.match(str(rel_path))
    if match:
        try:
            return Clip.objects.get(pk=match.group(1))
        except (Clip.DoesNotExist, ValueError, TypeError):
            pass
    return None


def _find_video_by_media_path(rel_path):
    """videos.Video whose file field matches the media-relative path."""
    from apps.videos.models import Video

    name = str(rel_path)
    if name.startswith("videos/"):
        return Video.objects.filter(file=name).first()
    return None


def resolve_media_item(item):
    """Resolve one OpenReel MediaItem into pipeline source keys.

    Returns a dict with any of ``video_id`` / ``clip_id`` / ``hd_file`` /
    ``source_url`` (the keys coerce_v12_payload understands).  Raises
    OpenReelConversionError when the media cannot be resolved at all.
    """
    item = item or {}
    url = item.get("originalUrl") or ""
    kind, rel = classify_media_url(url)

    if kind == "full":
        video = _find_video_by_media_path(rel)
        if video is not None:
            return {"video_id": video.pk}
        path = _media_path(rel)
        if path.is_file():
            return {"hd_file": str(path)}
        raise OpenReelConversionError(
            f"media item {item.get('id') or '?'}: full-quality file "
            f"{rel!r} is not present on this backend"
        )
    if kind == "preview":
        clip = _find_clip_by_proxy(rel)
        if clip is not None:
            entry = {"clip_id": str(clip.pk), "source_url": clip.source_url}
            if clip.hd_status == "ready" and clip.hd_file:
                entry["hd_file"] = clip.hd_file.name
            return entry
        # A proxy that belongs to no studio clip is still a playable local
        # file — prefer it over failing.
        path = _media_path(rel)
        if path.is_file():
            return {"hd_file": str(path)}
        raise OpenReelConversionError(
            f"media item {item.get('id') or '?'}: preview proxy {rel!r} "
            f"does not exist on this backend"
        )
    if kind == "media":
        path = _media_path(rel)
        if path.is_file():
            return {"hd_file": str(path)}
        raise OpenReelConversionError(
            f"media item {item.get('id') or '?'}: local file {rel!r} "
            f"does not exist"
        )
    if kind == "remote":
        return {"source_url": url}

    raise OpenReelConversionError(
        f"media item {item.get('id') or '?'} has no usable originalUrl"
    )


# ---------------------------------------------------------------------------
# Full conversion
# ---------------------------------------------------------------------------


def _timeline_video_clips(project):
    """(clip, media_item) for every visible video-track clip, by startTime."""
    library = {
        item.get("id"): item
        for item in (project.get("mediaLibrary") or {}).get("items") or []
        if isinstance(item, dict)
    }
    out = []
    for track in (project.get("timeline") or {}).get("tracks") or []:
        if not isinstance(track, dict) or track.get("hidden"):
            continue
        if track.get("type") not in (None, "video"):
            continue
        for clip in track.get("clips") or []:
            if not isinstance(clip, dict):
                continue
            media = library.get(clip.get("mediaId"))
            if media is None or media.get("type") not in ("video", None):
                continue
            out.append((clip, media))
    out.sort(key=lambda pair: (
        float(pair[0].get("startTime") or 0.0),
        str(pair[0].get("id") or ""),
    ))
    return out


def _overlay_for_clip(text_clips, clip_start, clip_end):
    """Text overlay whose window intersects the clip (earliest start)."""
    best = None
    for tc in text_clips:
        try:
            start = float(tc.get("startTime") or 0.0)
            dur = float(tc.get("duration") or 0.0)
        except (TypeError, ValueError):
            continue
        if start < clip_end and start + dur > clip_start:
            if best is None or start < best[0]:
                best = (start, tc)
    return best[1] if best else None


def convert_project(project_file, ranking=None):
    """Convert an OpenReel ProjectFile + ranking into a pipeline payload.

    Returns a v1.2-shaped payload (coerce_v12_payload compatible) extended
    with per-clip ``openreel_text`` overlay descriptors consumed by
    ``run_openreel_render``.
    """
    ranking = ranking or {}
    project = validate_project_file(project_file)

    # ---- aspect: ranking wins, else canvas orientation ----------------
    aspect = ranking.get("aspect")
    if aspect not in ("9:16", "16:9"):
        canvas = project.get("settings") or {}
        aspect = ("16:9" if _num(canvas, "width", 1080)
                  >= _num(canvas, "height", 1920) else "9:16")

    video_clips = _timeline_video_clips(project)
    if not video_clips:
        raise OpenReelConversionError(
            "project has no video clips on its timeline"
        )

    text_clips = [
        tc for tc in (project.get("textClips") or [])
        if isinstance(tc, dict) and tc.get("text")
    ]
    rank_by_media = {
        entry["mediaId"]: entry
        for entry in ranking.get("clips") or []
        if isinstance(entry, dict) and entry.get("mediaId")
    }

    clips_out = []
    seen_ranks = set()
    auto_rank = 0
    for clip, media in video_clips:
        media_id = media.get("id")
        entry = rank_by_media.get(media_id) or {}

        # ---- trim window: ranking → clip in/out → duration ------------
        start = entry.get("start")
        if start is None:
            start = clip.get("inPoint", 0.0)
        end = entry.get("end")
        if end is None:
            end = clip.get("outPoint")
        try:
            start = float(start)
        except (TypeError, ValueError):
            start = 0.0
        try:
            end = float(end) if end is not None else None
        except (TypeError, ValueError):
            end = None
        if end is None:
            dur = _num(clip, "duration", 0.0)
            end = start + (dur if dur > 0 else 5.0)
        if end <= start:
            raise OpenReelConversionError(
                f"clip {clip.get('id') or media_id}: empty trim window "
                f"({start:.3f}..{end:.3f})"
            )

        # ---- rank: ranking → next free integer ------------------------
        try:
            rank = int(entry.get("rank"))
        except (TypeError, ValueError):
            rank = None
        if rank is None:
            auto_rank += 1
            rank = auto_rank
        if rank in seen_ranks:
            raise OpenReelConversionError(
                f"clip ranks must be unique (duplicate rank {rank})"
            )
        seen_ranks.add(rank)

        # ---- title: ranking → media name ------------------------------
        title = entry.get("title")
        if title is None:
            title = media.get("name") or ""
        title = str(title)[:60]

        # ---- source ---------------------------------------------------
        source = resolve_media_item(media)
        pipeline_clip = {
            "rank": rank,
            "start": start,
            "end": end,
            "title": title,
        }
        if source.get("video_id") is not None:
            pipeline_clip["video_id"] = source["video_id"]
        if source.get("clip_id"):
            pipeline_clip["clip_id"] = source["clip_id"]
        if source.get("source_url"):
            pipeline_clip["source_url"] = source["source_url"]
        if source.get("hd_file"):
            pipeline_clip["hd_file"] = source["hd_file"]

        vol = _num(clip, "volume", None) if clip.get("volume") is not None \
            else None
        if vol is not None and abs(vol - 1.0) > 1e-6:
            pipeline_clip["volume"] = _clamp(vol, 0.0, 3.0)

        # ---- text overlay ---------------------------------------------
        overlay = _overlay_for_clip(text_clips, start, end)
        if overlay is not None:
            text_style = entry.get("textStyle") or overlay.get("style") or {}
            anim = dict(overlay.get("animation") or {})
            text_anim = entry.get("textAnim")
            if text_anim:
                anim["preset"] = text_anim
            overlay_start = 0.0
            try:
                overlay_start = max(0.0, float(overlay.get("startTime") or 0.0))
            except (TypeError, ValueError):
                pass
            overlay_dur = _num(overlay, "duration", 0.0) or (end - start)
            pipeline_clip["openreel_text"] = {
                "text": str(overlay.get("text") or ""),
                "style": text_style,
                "transform": overlay.get("transform") or {},
                "animation": anim if anim.get("preset") else None,
                "offset": _clamp(overlay_start, 0.0, max(0.0, end - start - 0.1)),
                "duration": min(overlay_dur, end - start),
            }

        clips_out.append(pipeline_clip)

    return {
        "master_title": str(ranking.get("master_title") or ""),
        "aspect": aspect,
        "clips": clips_out,
        "settings": {
            "video_height_pct": 80,
            "background_blur": True,
        },
    }


# ---------------------------------------------------------------------------
# Task entry point (scheduled by the API view via runner.submit)
# ---------------------------------------------------------------------------


def run_openreel_render(task, job_pk):
    """RenderJob worker: convert the stored OpenReel payload, then render.

    The stored payload is ``{"project": <ProjectFile>, "ranking": {...}}``.
    Conversion result is written onto the job row (auditable), then the
    existing run_render pipeline executes it.
    """
    from ..models import RenderJob
    from .pipeline import run_render

    try:
        job = RenderJob.objects.get(pk=job_pk)
    except RenderJob.DoesNotExist:
        raise OpenReelConversionError(f"RenderJob {job_pk} does not exist")

    payload = convert_project(
        job.payload.get("project"), job.payload.get("ranking")
    )
    job.payload = payload
    job.save(update_fields=["payload"])
    return run_render(task, job.pk)
