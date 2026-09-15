# Fonts — integrator notes (v1.2)

## What lives where

- TTF files: `backend/assets/fonts/*.ttf` (5 fonts, committed to the repo)
- Registry: `backend/assets/fonts/fonts.json` — friendly name → filename:

  | Friendly name          | Filename               |
  |------------------------|------------------------|
  | Archivo Black          | ArchivoBlack.ttf       |
  | Bebas Neue             | BebasNeue-Regular.ttf  |
  | Rubik Bold             | Rubik-Bold.ttf         |
  | Montserrat ExtraBold   | Montserrat-ExtraBold.ttf |
  | Vazirmatn Bold         | Vazirmatn-Bold.ttf     |

- Resolution logic: `backend/apps/studio/fonts.py`

## Resolution order (works locally AND in docker)

`apps.studio.fonts.FONT_DIR` is resolved at import time:

1. `VRS_FONT_DIR` env var (explicit override, wins if set)
2. `/app/assets/fonts` (container layout — backend copied to `/app`)
3. `<repo>/backend/assets/fonts` (local dev checkout)

There is **no FONT_DIR setting in settings/base.py** — the studio app owns
resolution via `apps.studio.fonts`. Consumers (render pipeline, future
`/api/fonts/` endpoint) should use:

```python
from apps.studio import fonts
path = fonts.font_path("Bebas Neue")   # absolute Path or None
fonts.available_fonts()                # friendly names present on disk
```

or, if a settings-based value is preferred:
`getattr(settings, "FONT_DIR", fonts.FONT_DIR)`.

## Docker mounting note (for whoever owns the Dockerfile)

The backend Dockerfile does **not** copy `assets/` (this app's owner doesn't
touch Dockerfiles). To make fonts available in the container, the
Dockerfile owner needs ONE of:

```dockerfile
# Option A — copy the assets next to the code (backend root = /app):
COPY backend/assets /app/assets

# Option B — mount at build/run time via docker-compose volume:
#   - ./backend/assets/fonts:/app/assets/fonts:ro
```

Since `FONT_DIR` checks `/app/assets/fonts` first (after the env override),
either option works without any settings change. Alternatively set
`VRS_FONT_DIR=/app/assets/fonts` as an explicit env var in the image.

## Adding a font later

1. Drop the `.ttf` into `backend/assets/fonts/`
2. Add `"Friendly Name": "Filename.ttf"` to `fonts.json`
3. No code changes needed — `fonts.py` reads the map at import time.
