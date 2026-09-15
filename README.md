# Viral Ranker Studio

A self-hosted, GPU-accelerated studio for building **vertical (9:16) video-ranking
compilations** from any downloadable source. Paste a video URL, trim and arrange the
clips into a ranked countdown, pick background music, and render a polished
1080×1920 master with numbered title cards — NVENC hardware encoding when an
NVIDIA GPU is available, automatic CPU fallback when it is not.

Everything runs locally with Docker Compose: a Django REST backend orchestrates
`yt-dlp` downloads and an `ffmpeg` render pipeline, while an nginx-served React
dashboard drives the whole workflow from your browser.

```
                                   ┌─────────────────────────────────────────────┐
                                   │  Docker Compose (viral-ranker-studio)       │
                                   │                                             │
  Browser ── :3000 ──────────────► │  ┌───────────────────────┐                  │
  (React dashboard, nginx)         │  │  frontend (nginx)     │                  │
                                   │  │  • React 18 + Vite    │                  │
  Browser ── :8000/api ──────────► │  │  • serves /media/*    │◄──┐              │
                                   │  │    (renders, thumbs)  │   │ shared       │
                                   │  └──────────┬────────────┘   │ bind mount   │
                                   │             │ proxies none,  │              │
                                   │             │ SPA calls API  │              │
                                   │             ▼                |              │
                                   │  ┌───────────────────────┐   |              │
                                   │  │  backend (gunicorn)   │   |              │
                                   │  │  • Django 4.2 + DRF   │   |              │
                                   │  │  • ThreadPool runner  │   |              │
                                   │  │  • yt-dlp downloads ──┼───┼──► ./media   │
                                   │  │  • ffmpeg renders ────┼───┘    (videos/,
                                   │  │  • ffprobe metadata   │         renders/,
                                   │  │  • SQLite ./data      │         thumbs/)
                                   │  └──────────┬────────────┘                  │
                                   │             │                               │
                                   │      NVIDIA GPU (runtime: nvidia)           │
                                   │      h264_nvenc ── fallback ──► libx264     │
                                   └─────────────────────────────────────────────┘
```

## Features

- **Fetch by URL** — paste any `yt-dlp`-supported link (YouTube, direct MP4, …);
  the video is downloaded in the background with progress polling.
- **Ranked timeline editor** — arrange clips into a countdown, trim start/end to
  the tenth of a second, give every clip a title.
- **Vertical master rendering** — 1080×1920 (9:16) output with a blurred-video
  background, adjustable content height, and numbered rank title cards.
- **Background music** — drop royalty-free tracks into `bgm/`; they are seeded
  automatically and mixed under the clips at a configurable volume.
- **NVENC hardware encoding** — uses `h264_nvenc` when an NVIDIA GPU is visible
  in the container; silently falls back to `libx264` otherwise. The encoder
  actually used is reported on every finished render task.
- **Async everything** — downloads and renders run as background tasks with
  live progress (`PENDING → PROCESSING → SUCCESS/FAILED`); the UI polls
  `GET /api/tasks/{id}/`.
- **Crash-safe** — tasks interrupted by a container restart are automatically
  failed at boot (`fail_stale_tasks`) instead of polling forever.

## Prerequisites

- **Ubuntu** (or any modern Linux) with **Docker Engine 24+** and **Docker Compose v2**.
- **NVIDIA GPU + driver** (optional but recommended) for hardware encoding.
  GTX 1660 SUPER-class GPUs and newer work; the render pipeline falls back to
  CPU encoding without one.
- **NVIDIA Container Toolkit** so containers can see the GPU:

  ```bash
  sudo apt-get install -y nvidia-container-toolkit
  sudo nvidia-ctk runtime configure --runtime=docker
  sudo systemctl restart docker
  ```

  > Using the **snap** Docker? Replace the middle command with
  > `sudo snap connect docker:nvidia-2404 nvidia-2404:gpu` style interface
  > wiring, or simply keep the `runtime: nvidia` line in `docker-compose.yml`
  > (the snap ships the nvidia runtime already registered). Verify with
  > `docker info | grep nvidia`.

- ~2 GB free disk for images, plus room for the videos you download and render.
- Ports **3000** (dashboard) and **8000** (API) free — configurable in `.env`.

## Quickstart

```bash
cd rank-viral            # this repository
cp .env.example .env     # or create .env with the values below (defaults are fine)

docker compose up -d --build
```

Open **http://localhost:3000** — the dashboard is served by nginx and talks to
the API on the same host at `http://localhost:8000`.

Minimal `.env` (the shipped file already works locally):

```dotenv
SECRET_KEY=change-me-to-a-long-random-string
DJANGO_SETTINGS_MODULE=config.settings.prod
BACKEND_PORT=8000
FRONTEND_PORT=3000
```

The first boot applies database migrations, fails stale tasks from any previous
run, seeds the BGM library from `bgm/`, and starts gunicorn behind the
healthchecked container (`/api/health/`).

## Usage guide

1. **Fetch** — in the dashboard, paste a video URL (e.g. a YouTube link or a
   direct `.mp4`) and hit *Fetch*. The backend returns a task id; the UI polls
   until the download finishes and a probe (`ffprobe`) records duration,
   resolution and audio presence.
2. **Arrange** — drag the fetched videos into your ranking order. Each entry
   becomes a numbered clip (`#1`, `#2`, …) with an editable title.
3. **Trim** — set `start` / `end` (seconds) per clip to cut to the good part.
   The timeline validates against the probed duration.
4. **Settings** — choose the content height (what % of the 1920px canvas the
   main video occupies), toggle the blurred-background fill, pick a BGM track
   from the dropdown (uploaded files appear after `seed_bgm` runs at boot) and
   its volume.
5. **Render** — hit *Render*. A render task runs the ffmpeg pipeline
   (concatenate trimmed clips → scale to 1080×1920 → blurred background →
   drawtext rank cards → mix BGM → encode). Progress is polled live; the
   finished task reports the output URL and the encoder used.
6. **Download** — the result card links to the rendered MP4, served straight
   from the shared `media/` volume by nginx. Right-click → save, or `curl` the
   `download_url`.

## API reference

Base URL: `http://localhost:8000/api`

| Method | Path                  | Body / params                                                                                          | Returns |
|--------|-----------------------|--------------------------------------------------------------------------------------------------------|---------|
| GET    | `/health/`            | —                                                                                                       | `200 {"status": "ok"}` (used by the Docker healthcheck) |
| POST   | `/fetch/`             | `{"url": "https://…"}`                                                                                  | `202 {"task_id": "<uuid>", …}` |
| GET    | `/tasks/{task_id}/`   | —                                                                                                       | `200 {"id", "task_type": "FETCH"\|"RENDER", "status": "PENDING"\|"PROCESSING"\|"SUCCESS"\|"FAILED", "progress": 0-100, "error", "result": {…}}` |
| GET    | `/videos/`            | —                                                                                                       | `200` list of downloaded videos (id, title, duration, width, height, has_audio, thumbnail URL, file URL) |
| GET    | `/videos/{id}/`       | —                                                                                                       | `200` single video detail |
| DELETE | `/videos/{id}/`       | —                                                                                                       | `204` (removes the DB row and the stored file) |
| POST   | `/render/`            | `{"master_title": "Top 5 …", "clips": [{"video_id": 1, "rank": 1, "start": 0.0, "end": 12.5, "title": "Clip title"}], "settings": {"video_height_pct": 80, "background_blur": true, "bgm_id": null, "bgm_volume": 0.4}}` | `202 {"task_id": "<uuid>", …}` |
| GET    | `/bgm/`               | —                                                                                                       | `200` list of seeded BGM tracks (id, name, duration, file URL) |

Successful task results: a **FETCH** task's `result` contains `video_url` (and
the new video id); a **RENDER** task's `result` contains `download_url`,
`duration`, and `encoder_used` (`h264_nvenc` or `libx264`).

Example — end-to-end with `curl`:

```bash
# 1. start a download
curl -s -X POST http://localhost:8000/api/fetch/ \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://samplelib.com/mp4/sample-5s.mp4"}'
# → {"task_id": "…", "status": "PENDING"}

# 2. poll until SUCCESS (video_url appears in result)
curl -s http://localhost:8000/api/tasks/<task_id>/

# 3. list videos, then render a 1-clip compilation
curl -s http://localhost:8000/api/videos/
curl -s -X POST http://localhost:8000/api/render/ \
  -H 'Content-Type: application/json' \
  -d '{"master_title": "Integration Test", "clips": [{"video_id": 1, "rank": 1, "start": 0.0, "end": 5.0, "title": "Test"}], "settings": {"video_height_pct": 80, "background_blur": true, "bgm_id": null, "bgm_volume": 0.4}}'

# 4. poll the render task, then download the master
curl -s http://localhost:8000/api/tasks/<render_task_id>/   # → result.download_url
curl -fO http://localhost:3000/media/<output-file>.mp4
```

## NVENC hardware encoding (and the CPU fallback)

The backend container is started with the `nvidia` runtime and
`NVIDIA_DRIVER_CAPABILITIES=compute,utility,video`, which mounts the host
driver's `libnvidia-encode.so` into the container. The render service:

1. tries a tiny `h264_nvenc` probe encode at startup of the render task;
2. if the probe succeeds → renders with `-c:v h264_nvenc` (fast, low CPU);
3. if the probe fails (no GPU, old driver, toolkit missing, codec disabled) →
   re-runs with `-c:v libx264 -preset veryfast` — output is identical in
   container/codec profile terms (H.264 MP4), just slower on CPU;
4. records which encoder was used in the task result (`encoder_used`), so the
   dashboard can show a "GPU" or "CPU" badge on the finished render.

**Note on ffmpeg versions:** very new ffmpeg builds (9+) require NVIDIA driver
≥ 610 for NVENC; the bundled Debian ffmpeg (7.1.x) works with driver 595+.
If you see `Driver does not support the required nvenc API version` in the
backend logs, either upgrade the host driver or rely on the automatic fallback
(it kicks in on its own).

## Where files live

| Host path                       | Container path        | Purpose                                                        |
|---------------------------------|-----------------------|----------------------------------------------------------------|
| `media/videos/`                 | `/app/media/videos`   | downloaded source videos (also visible to nginx at `/media/…`) |
| `media/renders/`                | `/app/media/renders`  | finished compilation MP4s — linked as the download URL          |
| `media/thumbs/`                 | `/app/media/thumbs`   | generated thumbnails                                           |
| `media/bgm/`                    | `/app/media/bgm`      | copies of the seeded BGM tracks                                |
| `bgm/`                          | `/app/bgm` (read-only)| your music library — drop `.mp3/.m4a/.wav/…` here              |
| `data/db.sqlite3`               | `/app/data`           | the SQLite database (tasks, videos, render jobs)               |
| `.env`                          | —                     | ports + Django secret key                                      |

## Troubleshooting

- **"NVENC unavailable" / renders report `libx264`** — the system still works
  (automatic fallback). To get GPU encoding back:
  - `docker info | grep -i nvidia` — is the runtime registered?
  - `dpkg -l | grep nvidia-container` — is the toolkit installed?
  - If not: `sudo apt-get install -y nvidia-container-toolkit && sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`
  - If you use snap Docker and the `deploy:`-style GPU stanza, prefer the
    `runtime: nvidia` key already present in `docker-compose.yml`.
- **Logs** — `docker compose logs -f backend` (or `frontend`). Render tasks
  also store their error text on the task: `GET /api/tasks/{id}/` → `error`.
- **Health check failing** — `curl -sf http://localhost:8000/api/health/` from
  the host; check `docker compose ps` and the backend logs for migration errors.
- **A task is stuck in PROCESSING forever** — restart the stack
  (`docker compose restart backend`); `fail_stale_tasks` marks orphaned tasks
  as FAILED at boot so the UI recovers.
- **Full reset (keep nothing)** —

  ```bash
  docker compose down -v
  sudo rm -rf media/* data/db.sqlite3
  docker compose up -d --build
  ```

  (`down -v` removes the containers/networks; `media/` and `data/` are bind
  mounts, so clearing them explicitly is required for a clean slate.)
- **Port already in use** — set `BACKEND_PORT` / `FRONTEND_PORT` in `.env` and
  `docker compose up -d` again.
- **Changed a BGM file but it does not show up** — the seed runs at container
  start; `docker compose restart backend`.

## Development

### Backend (Django)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_bgm            # optional: import ../bgm into the DB
python manage.py runserver 0.0.0.0:8000
```

`config.settings.dev` (default via `manage.py`) enables `DEBUG` and serves
`media/` under `/media/`. Tests: `python manage.py test` (each test run gets
its own throwaway SQLite database).

Useful management commands created by the pipelines:

| Command             | Purpose                                                    |
|---------------------|------------------------------------------------------------|
| `seed_bgm`          | (re)import tracks from `BGM_SOURCE_DIR` into the database   |
| `fail_stale_tasks`  | mark PROCESSING/PENDING tasks orphaned by a crash as FAILED |

### Frontend (React + Vite)

```bash
cd frontend
npm install
npm run dev        # Vite dev server with a proxy for /api → :8000
```

`npm run build` produces the static bundle that the Docker image serves
through nginx (together with `/media/` for renders and thumbnails).

### Rebuilding after changes

```bash
docker compose up -d --build       # rebuild images and restart
docker compose logs -f backend     # follow the pipeline
```
