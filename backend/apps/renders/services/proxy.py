"""720p proxy download service for the OpenReel editor (v1.3).

``POST /api/proxy/ {url}`` downloads a guaranteed-playable 720p H.264 mp4
of any source URL into ``MEDIA_ROOT/previews/<uuid>.mp4`` and returns
``{proxy_url, task_id}`` — the editor calls this BEFORE stream-info when
importing media (stream-info stays for metadata; the proxy fixes the
"video does not play" bug for exotic containers/codecs by normalizing
everything to a progressive mp4).

The download uses YtDlpDownloader with a 720p-capped format selector
(``best[height<=720][ext=mp4]/best[height<=720]/best``) and then, when the
result is not already an mp4/H.264 progressive file, remuxes/transcodes
with ffmpeg (reusing the fetch service's _ensure_mp4 + a light
transcode guard). Runs on the task runner (TYPE_FETCH) so the request
thread returns immediately; progress is reported through the Task row.
"""
import logging
import shutil
import uuid
from pathlib import Path

from django.conf import settings

from apps.core.runner import set_progress
from apps.videos.services.probe import ProbeError, probe_media

logger = logging.getLogger(__name__)

#: Prefer an already-mp4 stream at or below 720p, then any <=720p stream,
#: then anything (worst case: we transcode down).
PROXY_FORMAT_SELECTOR = (
    "best[height<=720][ext=mp4]/best[height<=720]/best"
)

#: Progress checkpoints.
P_STARTED = 2.0
P_DOWNLOAD_BEGIN = 5.0
P_DOWNLOAD_END = 80.0
P_TRANSCODE = 85.0
P_DONE = 100.0

#: Cap on the ffmpeg remux/transcode step.
FFMPEG_TIMEOUT = 10 * 60

#: H.264 codec names accepted as "already a playable proxy".
H264_CODECS = {"h264"}


class ProxyError(Exception):
    """Fatal proxy-download failure surfaced on the Task as error."""


def _proxy_dir():
    directory = Path(settings.MEDIA_ROOT) / "previews"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _staging_dir(task_id):
    directory = _proxy_dir() / ".staging" / str(task_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _is_playable_proxy(path):
    """True when *path* is an mp4 with an H.264 video stream at or below
    720p — i.e. plays in every browser <video> element."""
    try:
        facts = probe_media(path)
    except ProbeError:
        return False
    return (
        path.suffix.lower() == ".mp4"
        and facts.get("video_codec") in H264_CODECS
        and 0 < facts.get("height", 0) <= 720
    )


def _transcode_to_proxy(src, dest, task_id):
    """ffmpeg: scale to <=720 height, H.264 + AAC in a faststart mp4."""
    import subprocess

    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-y",
        "-i", str(src),
        "-vf", "scale=-2:'min(720,ih)':flags=bicubic",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(dest),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT
        )
    except subprocess.TimeoutExpired as exc:
        raise ProxyError(f"proxy transcode timed out after {FFMPEG_TIMEOUT}s") from exc
    except OSError as exc:
        raise ProxyError(f"failed to execute ffmpeg: {exc}") from exc
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        tail = (proc.stderr or "")[-800:]
        raise ProxyError(f"proxy transcode failed: ffmpeg exit "
                         f"{proc.returncode}.\n{tail}")


def run_proxy_download(task, url):
    """Task-worker entry point: download + normalize a 720p proxy.

    Returns ``{"proxy_url": "/media/previews/<uuid>.mp4", "task_id": ...}``
    on success (also stored on the Task result).
    """
    set_progress(task.id, P_STARTED)
    staging = _staging_dir(task.id)
    try:
        from apps.videos.services.downloader import YtDlpDownloader

        set_progress(task.id, P_DOWNLOAD_BEGIN)

        def to_progress_cb(percent):
            # downloader reports 5..90 within its own scale; remap onto
            # P_DOWNLOAD_BEGIN..P_DOWNLOAD_END.
            frac = max(0.0, min(1.0, (percent - 5.0) / 85.0))
            set_progress(
                task.id,
                P_DOWNLOAD_BEGIN
                + (P_DOWNLOAD_END - P_DOWNLOAD_BEGIN) * frac,
            )

        downloader = YtDlpDownloader(format_selector=PROXY_FORMAT_SELECTOR)
        result = downloader.download(
            url, staging, progress_cb=to_progress_cb
        )
        downloaded = Path(result.path)
        set_progress(task.id, P_DOWNLOAD_END)

        name = f"{uuid.uuid4()}.mp4"
        dest = _proxy_dir() / name
        if _is_playable_proxy(downloaded):
            shutil.move(str(downloaded), str(dest))
        else:
            set_progress(task.id, P_TRANSCODE)
            _transcode_to_proxy(downloaded, dest, task.id)

        set_progress(task.id, P_DONE)
        return {
            "proxy_url": f"/media/previews/{name}",
            "task_id": str(task.id),
        }
    except RuntimeError as exc:
        # YtDlpDownloader wraps every failure in RuntimeError.
        raise ProxyError(str(exc)) from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        # drop the .staging root when this was its last tenant
        try:
            (staging.parent).rmdir()
        except OSError:
            pass
