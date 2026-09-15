"""Orchestration of the video fetch pipeline.

``run_fetch`` is the callable handed to :mod:`apps.core.runner` by the API
worker (a ThreadPool). It downloads a source URL with
:class:`~apps.videos.services.downloader.YtDlpDownloader` in a single yt-dlp
pass, guarantees an mp4 container, produces a thumbnail (yt-dlp's own, or an
ffmpeg frame grab as fallback), probes the final file with ffprobe and
persists a :class:`~apps.videos.models.Video` row.

Progress checkpoints (the download itself maps its own 5-90 % range):

    2    pipeline started
    5-90 download progress (via ``runner.set_progress``)
    97   Video row created
    100  done, just before returning
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from django.conf import settings

from apps.core.runner import set_progress
from apps.videos.models import Video
from apps.videos.services.downloader import YtDlpDownloader
from apps.videos.services.probe import ProbeError, probe_media

logger = logging.getLogger(__name__)

#: Staging area for in-flight downloads, relative to MEDIA_ROOT.
STAGING_ROOT = Path("videos") / ".staging"

#: Progress checkpoints (see module docstring).
P_STARTED = 2.0
P_VIDEO_SAVED = 97.0
P_DONE = 100.0

_FFMPEG_TIMEOUT = 600


def run_fetch(task, url):
    """Download *url* and store it as a Video; return a result payload.

    Designed to be called in a worker thread with a freshly created
    :class:`~apps.core.models.Task`. Raises :class:`RuntimeError` with a
    short human-readable message on any failure; the runner turns that into
    the task error. The returned dict is JSON-serializable and uses
    root-relative (same-origin) media URLs only.
    """
    set_progress(task.id, P_STARTED)
    staging = Path(settings.MEDIA_ROOT) / STAGING_ROOT / str(task.id)
    try:
        result = YtDlpDownloader().download(
            url, staging, progress_cb=lambda pct: set_progress(task.id, pct)
        )
        media = _ensure_mp4(result.path)
        facts = _probe_facts(media)
        thumbnail = _prepare_thumbnail(result.thumbnail, media)
        video = _persist_video(url, result.info, facts, media, thumbnail)
    finally:
        # Best-effort cleanup of leftovers (.part files, unconverted
        # thumbnails, pre-merge parts). The kept outputs were already moved
        # out of staging at this point.
        shutil.rmtree(staging, ignore_errors=True)
        # Also drop the (now empty) per-task staging parent so no
        # media/videos/.staging debris lingers after a successful fetch.
        try:
            staging.parent.rmdir()
        except OSError:
            pass  # not empty or already gone — nothing to do
    set_progress(task.id, P_VIDEO_SAVED)
    payload = {
        "video_id": str(video.pk),
        "title": video.title,
        "duration": video.duration,
        "width": video.width,
        "height": video.height,
        "has_audio": video.has_audio,
        "thumbnail_url": _media_url(video.thumbnail.name) if video.thumbnail else None,
        "video_url": _media_url(video.file.name),
    }
    set_progress(task.id, P_DONE)
    return payload


# -- pipeline steps ---------------------------------------------------------


def _probe_facts(path: Path) -> dict:
    """ffprobe the final file and enforce a usable duration."""
    try:
        facts = probe_media(path)
    except ProbeError as exc:
        raise RuntimeError(f"failed to probe downloaded file {path.name}: {exc}") from exc
    if facts["duration"] <= 0:
        raise RuntimeError(f"downloaded file {path.name} has no usable duration")
    return facts


def _ensure_mp4(path: Path) -> Path:
    """Guarantee an mp4 container.

    The format selector almost always yields mp4 already; when yt-dlp had to
    fall back to a foreign container, remux losslessly first and fall back to
    a real transcode only when the codecs are not mp4-compatible.
    """
    if path.suffix.lower() == ".mp4":
        return path
    target = path.with_suffix(".mp4")
    if not _run_ffmpeg(["-y", "-i", str(path), "-c", "copy", str(target)]):
        _run_ffmpeg(
            [
                "-y",
                "-i",
                str(path),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(target),
            ]
        )
    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError(
            f"could not convert downloaded {path.suffix or 'media'} file to mp4"
        )
    return target


def _prepare_thumbnail(ytdlp_thumbnail: Optional[Path], media: Path) -> Optional[Path]:
    """Return a jpg for the video, or None if none could be produced.

    Prefers the thumbnail yt-dlp downloaded (converting it to jpg when
    needed); when the source had no thumbnail at all — plain file URLs never
    do — grabs a frame from the downloaded video with ffmpeg instead.
    """
    if ytdlp_thumbnail is not None and ytdlp_thumbnail.is_file():
        if ytdlp_thumbnail.suffix.lower() in (".jpg", ".jpeg"):
            return ytdlp_thumbnail
        converted = ytdlp_thumbnail.with_suffix(".jpg")
        if _run_ffmpeg(["-y", "-i", str(ytdlp_thumbnail), "-frames:v", "1", str(converted)]):
            return converted
        logger.warning("could not convert thumbnail %s to jpg", ytdlp_thumbnail)

    frame = media.with_name(f"{media.stem}-thumb.jpg")
    if _run_ffmpeg(
        ["-y", "-ss", "1", "-i", str(media), "-frames:v", "1", "-vf", "scale=640:-2", str(frame)]
    ):
        return frame
    # Very short clips may not have a frame at the 1 second mark; try the
    # very start before giving up (a missing thumbnail is not fatal).
    logger.warning("frame grab at 1s failed for %s; retrying at 0s", media)
    if _run_ffmpeg(
        ["-y", "-ss", "0", "-i", str(media), "-frames:v", "1", "-vf", "scale=640:-2", str(frame)]
    ):
        return frame
    logger.warning("could not produce any thumbnail for %s", media)
    return None


def _persist_video(url, info, facts, media, thumbnail) -> Video:
    """Move the outputs into MEDIA_ROOT and create the Video row."""
    file_name = f"videos/{uuid.uuid4()}.mp4"
    _move_into_media(media, file_name)

    thumbnail_name = None
    if thumbnail is not None:
        thumbnail_name = f"thumbs/{uuid.uuid4()}.jpg"
        try:
            _move_into_media(thumbnail, thumbnail_name)
        except OSError:
            logger.warning("could not store thumbnail for %s", file_name, exc_info=True)
            thumbnail_name = None

    try:
        return Video.objects.create(
            source_url=url,
            title=_title_from(info, media, url),
            duration=facts["duration"],
            width=facts["width"],
            height=facts["height"],
            has_audio=facts["has_audio"],
            thumbnail=thumbnail_name,
            file=file_name,
        )
    except Exception:
        # Never leave orphaned media behind when the row cannot be saved.
        for name in filter(None, (file_name, thumbnail_name)):
            try:
                (Path(settings.MEDIA_ROOT) / name).unlink(missing_ok=True)
            except OSError:
                pass
        raise


# -- helpers ----------------------------------------------------------------


def _title_from(info: dict, media: Path, url: str) -> str:
    """Video title from yt-dlp metadata, truncated to the field limit."""
    title = str(info.get("title") or "").strip()
    if not title:
        title = media.stem.replace("_", " ").strip() or url
    return title[:500]


def _move_into_media(src: Path, name: str) -> Path:
    """Move *src* to ``MEDIA_ROOT/<name>`` creating parent dirs as needed."""
    dest = Path(settings.MEDIA_ROOT) / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return dest


def _media_url(name: str) -> str:
    """Root-relative URL for a stored media file (same-origin frontend)."""
    base = (settings.MEDIA_URL or "/media/").rstrip("/")
    return f"{base}/{name.lstrip('/')}"


def _run_ffmpeg(args) -> bool:
    """Run ffmpeg best-effort; return True on success, never raise."""
    cmd = ["ffmpeg", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("ffmpeg %s failed: %s", args[:4], exc)
        return False
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        logger.warning(
            "ffmpeg %s failed (exit %s): %s",
            args[:4],
            proc.returncode,
            detail[-1] if detail else "",
        )
        return False
    return True
