"""Drawtext-safe escaping, font resolution and typography helpers.

FFmpeg's drawtext filter treats ``:`` as an option separator, ``'`` as a
quoting character and ``\\`` as an escape character inside the filtergraph
description — and the whole filtergraph is itself parsed by a shell-like
quoting layer on top.  Rather than fighting three stacked quoting layers we
write risky text (titles) to a temp file and reference it with ``textfile=``,
and escape the short rank badges ("#3") with :func:`escape_text`.

v1.2 additions:

* friendly-name font resolution against the renders/studio font dirs
  (ArchivoBlack, BebasNeue, Rubik, Montserrat, Vazirmatn + system fallback)
* clip style coercion (:func:`coerce_style`) and drawtext color normalization
* RTL awareness: this ffmpeg build ships libfribidi (verified: the
  ``text_shaping`` drawtext option exists and produces correctly shaped,
  bidi-ordered Arabic script).  :func:`rtl_fix` is therefore a no-op here,
  but keeps a naive Arabic-run reversal fallback for ffmpeg builds without
  fribidi so Persian titles never render as disconnected LTR gibberish.
"""
import os
import re
import subprocess
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Escaping (unchanged v1.1 behaviour)
# ---------------------------------------------------------------------------

def escape_text(text):
    """Escape *text* for use in a drawtext ``text=`` option.

    Escapes, in order: backslash, colon, apostrophe, percent (drawtext can
    expand ``%{...}`` expansions).  The result must be embedded in the
    filtergraph string inside single quotes.
    """
    text = str(text)
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\u2019")  # typographic apostrophe — safe literal
    text = text.replace("%", "\\%")
    return text


def write_text_file(text, directory=None, prefix="vrs_text_"):
    """Write *text* to a UTF-8 temp file and return its absolute path.

    The caller is responsible for the file's lifetime (the render pipeline
    keeps a staging dir and removes it wholesale).
    """
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".txt", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(rtl_fix(str(text)))
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


# ---------------------------------------------------------------------------
# RTL / Arabic-script handling
# ---------------------------------------------------------------------------

#: Arabic + Arabic Supplement + Arabic Extended-A + presentation forms.
ARABIC_RE = re.compile(
    "[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]"
)


def has_arabic(text):
    """True when *text* contains Arabic-script codepoints."""
    return bool(ARABIC_RE.search(str(text)))


def _is_arabic_char(ch):
    return bool(ARABIC_RE.match(ch))


_ff_fribidi = None


def ffmpeg_has_fribidi():
    """True when the ffmpeg build exposes drawtext text_shaping (libfribidi).

    Detected once per process by parsing ``ffmpeg -h filter=drawtext``.
    """
    global _ff_fribidi
    if _ff_fribidi is None:
        try:
            proc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-h", "filter=drawtext"],
                capture_output=True, text=True, timeout=30,
            )
            blob = (proc.stdout or "") + (proc.stderr or "")
            _ff_fribidi = "text_shaping" in blob
        except (OSError, subprocess.SubprocessError):
            _ff_fribidi = False
    return _ff_fribidi


def _reverse_arabic_runs(text):
    """Naive visual-order fallback: reverse each contiguous Arabic run.

    Best-effort only — proper bidi needs the Unicode bidi algorithm and
    proper shaping needs HarfBuzz/fribidi.  Used solely when the host ffmpeg
    lacks fribidi (not the case for this project's ffmpeg 8.0.1).
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        if _is_arabic_char(text[i]):
            j = i
            while j < n and (_is_arabic_char(text[j]) or text[j] == " "):
                j += 1
            out.append(text[i:j][::-1])
            i = j
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def rtl_fix(text):
    """Return *text* ready for drawtext.

    With fribidi (this build) text passes through unchanged — ffmpeg shapes
    and reorders it correctly.  Without fribidi, Arabic runs are reversed to
    visual order so at least word/letter order reads right-to-left.
    """
    text = str(text)
    if not has_arabic(text):
        return text
    if ffmpeg_has_fribidi():
        return text
    return _reverse_arabic_runs(text)


# ---------------------------------------------------------------------------
# Font resolution (v1.2)
# ---------------------------------------------------------------------------

#: …/backend/apps/renders/assets/fonts
_RENDERS_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
#: …/backend
_BACKEND_DIR = _RENDERS_FONT_DIR.parent.parent

_font_dirs = None


def font_dirs():
    """Return the list of existing font directories, in priority order.

    1. ``VRS_FONT_DIR`` env var (explicit override)
    2. ``apps/renders/assets/fonts`` (static bold instances, this app)
    3. ``backend/assets/fonts`` (shared studio font dir, same filenames
       where possible)
    4. ``/app/assets/fonts`` (container layout)
    """
    global _font_dirs
    if _font_dirs is None:
        candidates = []
        env = os.environ.get("VRS_FONT_DIR")
        if env:
            candidates.append(Path(env))
        candidates.append(_RENDERS_FONT_DIR)
        candidates.append(_BACKEND_DIR / "assets" / "fonts")
        candidates.append(Path("/app/assets/fonts"))
        _font_dirs = [c for c in candidates if c.is_dir()] or [_RENDERS_FONT_DIR]
    return list(_font_dirs)


#: friendly/canonical name (lowercased) → candidate filenames.
FONT_ALIASES = {
    "archivoblack": ("ArchivoBlack.ttf",),
    "archivo black": ("ArchivoBlack.ttf",),
    "bebasneue": ("BebasNeue.ttf", "BebasNeue-Regular.ttf"),
    "bebas neue": ("BebasNeue.ttf", "BebasNeue-Regular.ttf"),
    "rubik": ("Rubik-Bold.ttf",),
    "rubik bold": ("Rubik-Bold.ttf",),
    "montserrat": ("Montserrat-Bold.ttf", "Montserrat-ExtraBold.ttf"),
    "montserrat bold": ("Montserrat-Bold.ttf", "Montserrat-ExtraBold.ttf"),
    "montserrat extrabold": ("Montserrat-ExtraBold.ttf", "Montserrat-Bold.ttf"),
    "vazirmatn": ("Vazirmatn-Bold.ttf",),
    "vazirmatn bold": ("Vazirmatn-Bold.ttf",),
    "liberationsans": (),
    "liberation sans": (),
    "liberationsans-bold": (),
    "default": (),
    "": (),
}


def resolve_style_font(name):
    """Resolve a style ``font`` name to a concrete TTF path (or None).

    Accepts the friendly names used by the studio (``"Archivo Black"``,
    ``"Vazirmatn Bold"``…), the compact names from the render spec
    (``"ArchivoBlack"``, ``"Rubik"``…) and bare filenames.  Returns ``None``
    for unknown/missing fonts — callers fall back to the system
    LiberationSans-Bold via ``filters.resolve_font()``.
    """
    if not name:
        return None
    raw = str(name).strip()
    key = raw.lower()
    filenames = FONT_ALIASES.get(key)
    if filenames is None:
        # Unknown name: treat it as a filename inside one of the font dirs.
        filenames = (raw,)
    for directory in font_dirs():
        for filename in filenames:
            path = directory / filename
            if path.is_file():
                return str(path)
    return None


# ---------------------------------------------------------------------------
# Style coercion + drawtext colour helpers
# ---------------------------------------------------------------------------

#: v1.1-equivalent defaults: LiberationSans-Bold look, bottom-centered title.
DEFAULT_TEXT_STYLE = {
    "font": None,
    "fontSize": 62,
    "textColor": "white",
    "strokeColor": "black",
    "strokeWidth": 0,
    "shadow": None,
    "badgeBg": "black",
    "badgeOpacity": 0.55,
    "textPosition": "bottom",
    "margin": None,
}


def coerce_style(style, base=None):
    """Merge a style JSON dict onto *base* (defaults: v1.1 look).

    Unknown keys are ignored; known keys are type-clamped so a hostile or
    sloppy payload can never produce a broken filtergraph.
    """
    merged = dict(base or DEFAULT_TEXT_STYLE)
    if not isinstance(style, dict):
        return merged
    for key in merged:
        if style.get(key) is not None:
            merged[key] = style[key]

    def _clamp_int(value, lo, hi, fallback):
        try:
            return max(lo, min(hi, int(float(value))))
        except (TypeError, ValueError):
            return fallback

    merged["fontSize"] = _clamp_int(merged["fontSize"], 8, 400, 62)
    merged["strokeWidth"] = _clamp_int(merged["strokeWidth"], 0, 20, 0)
    try:
        merged["badgeOpacity"] = max(0.0, min(1.0, float(merged["badgeOpacity"])))
    except (TypeError, ValueError):
        merged["badgeOpacity"] = 0.55
    if merged["textPosition"] not in ("top", "center", "bottom"):
        merged["textPosition"] = "bottom"
    if merged.get("margin") is not None:
        try:
            merged["margin"] = max(0, int(float(merged["margin"])))
        except (TypeError, ValueError):
            merged["margin"] = None
    return merged


def normalize_color(value, default="white"):
    """Normalize a colour for drawtext: names pass through, #rrggbb → 0xrrggbb."""
    text = str(value or "").strip()
    if not text:
        return default
    if text.startswith("#"):
        return "0x" + text[1:]
    return text
