"""Server-side FULL-quality render straight from source links (v1.4).

``POST /api/render/urls/`` accepts ranked clips referenced by remote URLs;
the worker downloads each unique URL exactly ONCE (default best-quality
format selector — never the 720p proxy selector), trims each clip to its
[start, end] window and feeds the existing render pipeline.

Source resolution order per unique URL:

1. A previously downloaded FULL-quality file for the same URL (persistent
   sha256(url)-addressed cache under ``MEDIA_ROOT/temp/fullcache/``).
2. A matching :class:`~apps.videos.models.Video` row whose ``source_url``
   matches and whose file still exists on disk.
3. A fresh full-quality download with the default format selector
   (bestvideo+bestaudio merge), moved into the cache for future jobs.

Progress: download 0-30, normalize 30-85, concat/encode 85-100 (the
normalize/concat windows are passed to ``run_render``).
"""
import hashlib
import logging
import shutil
from pathlib import Path

from django.conf import settings

from apps.core.runner import set_progress
from apps.renders.models import RenderJob
from apps.videos.models import Video

logger = logging.getLogger(__name__)

#: Progress windows on the 0-100 task scale.
P_DOWNLOAD_START = 0.0
P_DOWNLOAD_END = 30.0
P_NORMALIZE_START = 30.0
P_NORMALIZE_END = 85.0


class UrlsRenderError(Exception):
    """Fatal urls-render failure surfaced on the Task as ``error``."""


# ---------------------------------------------------------------------------
# Full-quality cache
# ---------------------------------------------------------------------------

def _fullcache_dir():
    directory = Path(settings.MEDIA_ROOT) / "temp" / "fullcache"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def url_cache_key(url):
    """Stable cache key for a source URL (matches the proxy scheme)."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def cached_full_file(url):
    """Return the cached full-quality file for *url*, or None."""
    candidate = _fullcache_dir() / f"{url_cache_key(url)}.mp4"
    if candidate.is_file() and candidate.stat().st_size > 0:
        return candidate
    return None


def _find_video_file(url):
    """A Video row for *url* with its file still present, or None."""
    for video in Video.objects.filter(source_url=url):
        try:
            path = Path(video.file.path)
        except (NotImplementedError, ValueError):
            continue
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _download_full(task, url, index, total):
    """Resolve *url* to a local FULL-quality file; returns its path.

    Prefer the persistent cache, then a matching Video row, then a fresh
    default-selector download (moved into the cache for future jobs — the
    returned path therefore never lives inside per-download staging).
    """
    span = P_DOWNLOAD_END - P_DOWNLOAD_START

    def mark(frac):
        set_progress(
            task.id,
            P_DOWNLOAD_START + span * ((index + min(1.0, max(0.0, frac)))
                                       / total),
        )

    # 1. persistent full-quality cache from a previous job
    cached = cached_full_file(url)
    if cached is not None:
        mark(1.0)
        logger.info("urls render: cache hit for %s", url)
        return cached

    # 2. an already-fetched Video for the same source URL
    existing = _find_video_file(url)
    if existing is not None:
        mark(1.0)
        logger.info("urls render: reusing Video file for %s", url)
        return existing

    # 3. fresh full-quality download (DEFAULT selector, never the proxy)
    mark(0.05)
    from apps.videos.services import fetch as fetch_svc
    from apps.videos.services.downloader import YtDlpDownloader

    def to_progress_cb(percent):
        # downloader reports 5..90 on its own scale; remap into this
        # download's slice of the 0-30 window.
        frac = max(0.0, min(1.0, (percent - 5.0) / 85.0))
        mark(0.05 + 0.95 * frac)

    staging = (Path(settings.MEDIA_ROOT) / "temp" / ".urls-staging"
               / f"{task.id}-{url_cache_key(url)}")
    staging.mkdir(parents=True, exist_ok=True)
    try:
        try:
            result = YtDlpDownloader().download(
                url, staging, progress_cb=to_progress_cb
            )
            media = fetch_svc._ensure_mp4(result.path)
        except RuntimeError as exc:
            raise UrlsRenderError(f"failed to download {url}: {exc}") from exc

        # move into the persistent cache so future jobs (and the other
        # clips of THIS job) reuse the file instead of re-downloading
        dest = _fullcache_dir() / f"{url_cache_key(url)}.mp4"
        try:
            shutil.move(str(media), str(dest))
        except OSError as exc:
            raise UrlsRenderError(
                f"could not store downloaded source for {url}: {exc}"
            ) from exc
        mark(1.0)
        return dest
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            staging.parent.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Task entry point (scheduled by the API view via the task runner)
# ---------------------------------------------------------------------------

def run_urls_render(task, job_pk):
    """RenderJob worker: resolve source URLs to full files, then render.

    The stored payload is the ``build_urls_payload`` shape.  Each clip is
    rewritten with ``full_file`` (one download per unique URL) and handed
    to the standard pipeline with the urls progress windows
    (normalize 30-85, concat 85-100).
    """
    from apps.renders.services.pipeline import run_render

    try:
        job = RenderJob.objects.get(pk=job_pk)
    except RenderJob.DoesNotExist:
        raise UrlsRenderError(f"RenderJob {job_pk} does not exist")

    payload = job.payload
    clips = list(payload.get("clips") or [])
    if not clips:
        raise UrlsRenderError("payload has no clips")

    # ---- resolve every unique source URL exactly once ----------------
    unique_urls = []
    seen = set()
    for clip in clips:
        url = clip.get("source_url")
        if not url:
            raise UrlsRenderError("clip is missing source_url")
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)

    resolved = {}
    for i, url in enumerate(unique_urls):
        resolved[url] = _download_full(task, url, i, len(unique_urls))

    # ---- rewrite the payload: full_file instead of source_url ---------
    new_clips = []
    for clip in clips:
        entry = dict(clip)
        entry.pop("source_url", None)
        entry["full_file"] = str(resolved[clip["source_url"]])
        new_clips.append(entry)

    job.payload = {**payload, "clips": new_clips}
    job.save(update_fields=["payload"])

    return run_render(
        task, job.pk,
        normalize_window=(P_NORMALIZE_START, P_NORMALIZE_END),
    )
