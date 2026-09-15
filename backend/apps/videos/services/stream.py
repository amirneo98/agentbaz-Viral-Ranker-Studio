"""Zero-wait stream extraction for the studio preview player (v1.2).

Extracts metadata and a direct playback URL with yt-dlp *without* writing
any media bytes to disk, so the frontend can start an HTML5 ``<video>``
preview within a couple of seconds of the user pasting a URL. Both yt-dlp
invocations follow the project's bot-evasion standards (desktop user agent,
socket timeout, retries) and share a hard 30-second wall-clock budget; when
the stream URL cannot be extracted the caller still gets the metadata with
``stream_type: "none"`` and falls back to the full ``/api/fetch/`` download.

yt-dlp is invoked as a subprocess here (unlike the downloader module, which
uses it as a library) because ``-j``/``-g`` are one-shot CLI modes whose
output maps directly onto this module's return shape.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import time

logger = logging.getLogger(__name__)

#: Desktop user agent per the media-ingestion standards (bot evasion).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

#: Format selector for a directly playable preview stream: prefer a
#: progressive mp4 capped at 720p, then anything <=720p, then anything.
STREAM_FORMAT = "best[ext=mp4][height<=720]/best[height<=720]/best"

#: Fallback selector for sites that no longer serve progressive (pre-merged
#: audio+video) formats — notably YouTube in 2026, where every format is
#: video-only or audio-only. ``-g`` then prints TWO urls (video, audio);
#: the video url becomes ``stream_url`` and the audio one ``audio_url``
#: (an additive field the frontend may attach as a second element).
STREAM_FORMAT_FALLBACK = "bestvideo*+bestaudio/best"

#: Hard wall-clock budget for the whole get_stream_info() call.
TIMEOUT_SECONDS = 30

#: Per-invocation yt-dlp network options (skill: socket timeout + retries).
_NET_OPTS = ["--socket-timeout", "15", "--retries", "3"]


class StreamInfoError(RuntimeError):
    """Raised when yt-dlp metadata extraction fails entirely."""


def yt_dlp_command():
    """Return the argv prefix that invokes yt-dlp.

    Prefers a ``yt-dlp`` binary on PATH (docker image, activated venv) and
    falls back to the current interpreter's yt_dlp module (backend venv).
    """
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    return [sys.executable, "-m", "yt_dlp"]


def get_stream_info(url):
    """Return preview metadata + direct stream URL for *url*.

    Returns a dict::

        {"title": str, "duration": float, "thumbnail": str | None,
         "stream_url": str | None, "stream_type": "mp4" | "hls" | "none"}

    Raises :class:`StreamInfoError` only when the metadata extraction
    itself fails (bad URL, unreachable site, timeout); a failed stream-URL
    extraction degrades to ``stream_type: "none"`` with metadata intact.
    """
    deadline = time.monotonic() + TIMEOUT_SECONDS
    meta = _extract_metadata(url, deadline)
    stream_url, stream_type, audio_url = _extract_stream_url(url, deadline)
    return {
        "title": str(meta.get("title") or ""),
        "duration": _to_float(meta.get("duration")) or 0.0,
        "thumbnail": meta.get("thumbnail"),
        "stream_url": stream_url,
        "stream_type": stream_type,
        # Present only when the source has no progressive format and the
        # fallback selector returned separate video/audio urls (YouTube).
        "audio_url": audio_url,
    }


# -- extraction steps -------------------------------------------------------


def _extract_metadata(url, deadline):
    """``yt-dlp -j --no-playlist --skip-download`` — metadata only."""
    cmd = [
        *yt_dlp_command(),
        "-j",
        "--no-playlist",
        "--skip-download",
        "--user-agent",
        USER_AGENT,
        *_NET_OPTS,
        url,
    ]
    proc = _run(cmd, deadline)
    if proc.returncode != 0:
        detail = _last_line(proc.stderr) or f"exit code {proc.returncode}"
        raise StreamInfoError(f"yt-dlp could not read video info: {detail}")
    try:
        data = json.loads(proc.stdout or "")
    except json.JSONDecodeError as exc:
        raise StreamInfoError("yt-dlp returned invalid metadata JSON") from exc
    if not isinstance(data, dict):
        raise StreamInfoError("yt-dlp returned unexpected metadata")
    # Playlists: keep the first entry, matching the fetch pipeline.
    if data.get("_type") == "playlist":
        entries = [e for e in (data.get("entries") or []) if isinstance(e, dict)]
        if not entries:
            raise StreamInfoError("playlist contains no videos")
        data = entries[0]
    return data


def _extract_stream_url(url, deadline):
    """``yt-dlp -g -f <STREAM_FORMAT>`` — direct playback URL.

    Never raises: any failure degrades to ``(None, "none")`` so the caller
    can still return metadata (frontend falls back to /api/fetch/).
    """
    for selector in (STREAM_FORMAT, STREAM_FORMAT_FALLBACK):
        cmd = [
            *yt_dlp_command(),
            "-g",
            "-f",
            selector,
            "--no-playlist",
            "--user-agent",
            USER_AGENT,
            *_NET_OPTS,
            url,
        ]
        try:
            proc = _run(cmd, deadline)
        except StreamInfoError as exc:
            logger.warning("stream URL extraction failed for %s: %s", url, exc)
            return None, "none", None
        if proc.returncode != 0:
            logger.warning(
                "stream URL extraction failed for %s (selector %s, exit %s): %s",
                url,
                selector,
                proc.returncode,
                _last_line(proc.stderr),
            )
            continue
        lines = [
            line.strip() for line in (proc.stdout or "").splitlines() if line.strip()
        ]
        if not lines:
            continue
        # With the merge fallback selector yt-dlp prints video+audio urls;
        # the first is the video stream the <video> tag needs.
        stream_url = lines[0]
        audio_url = lines[1] if len(lines) > 1 else None
        return stream_url, _classify(stream_url), audio_url
    return None, "none", None


def _classify(stream_url):
    """Map a direct URL onto the 'mp4' | 'hls' stream types."""
    path = stream_url.split("?", 1)[0].lower()
    if ".m3u8" in path:
        return "hls"
    return "mp4"


# -- helpers ----------------------------------------------------------------


def _run(cmd, deadline):
    """Run *cmd* with whatever is left of the shared time budget."""
    remaining = max(1.0, deadline - time.monotonic())
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=remaining
        )
    except subprocess.TimeoutExpired as exc:
        raise StreamInfoError(
            f"yt-dlp timed out after {TIMEOUT_SECONDS}s"
        ) from exc
    except OSError as exc:
        raise StreamInfoError(f"failed to execute yt-dlp: {exc}") from exc


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _last_line(text):
    lines = (text or "").strip().splitlines()
    return lines[-1].strip()[:300] if lines else ""
