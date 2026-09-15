"""Font directory resolution + registry for the studio typography engine.

The TTF files live in ``backend/assets/fonts``. This module resolves that
directory in a way that works both for the local checkout and for the
docker image (where the backend is copied to ``/app`` and assets to
``/app/assets``):

    1. ``VRS_FONT_DIR`` environment variable (explicit override)
    2. ``<backend>/assets/fonts`` (local dev + container, same tree)
    3. ``/app/assets/fonts`` (container with assets copied separately)

Resolution is exposed via :data:`FONT_DIR` (a :class:`pathlib.Path`) and
:data:`FONT_MAP` (friendly name → filename, from ``fonts.json``). The
renders pipeline and the ``/api/fonts/`` endpoint consume these.

Consumers that prefer a Django setting can use
``getattr(settings, "FONT_DIR", fonts.FONT_DIR)``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

#: Backend project root (…/backend) — this file is apps/studio/fonts.py.
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

_CANDIDATES = [
    Path("/app/assets/fonts"),  # container layout
    BACKEND_DIR / "assets" / "fonts",  # local checkout
]


def resolve_font_dir() -> Path:
    """Return the font directory, preferring VRS_FONT_DIR, then /app, then repo."""
    env = os.environ.get("VRS_FONT_DIR")
    if env:
        return Path(env)
    for candidate in _CANDIDATES:
        if candidate.is_dir():
            return candidate
    # Default to the repo layout even when missing (fonts may not be
    # downloaded yet); callers handle a non-existent directory.
    return BACKEND_DIR / "assets" / "fonts"


FONT_DIR = resolve_font_dir()

FONT_MAP_PATH = FONT_DIR / "fonts.json"


def load_font_map() -> dict:
    """Return the friendly-name → filename mapping from fonts.json."""
    try:
        data = json.loads(FONT_MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


FONT_MAP = load_font_map()


def font_path(friendly_name: str):
    """Return the absolute Path for a friendly font name, or None."""
    filename = FONT_MAP.get(friendly_name)
    if not filename:
        return None
    path = FONT_DIR / filename
    return path if path.is_file() else None


def available_fonts() -> list:
    """List of friendly names whose TTF actually exists on disk."""
    return [name for name in FONT_MAP if font_path(name) is not None]
