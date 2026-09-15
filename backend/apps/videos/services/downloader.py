"""yt-dlp wrapper isolating every yt-dlp specific detail of the fetch pipeline.

The rest of the project (fetch orchestration, views, tests) only sees plain
Python types: :class:`pathlib.Path` objects and info dicts. yt-dlp is used
strictly as a library (``import yt_dlp``), never as a subprocess, and keeping
the import inside this module makes the error path trivial to mock in tests.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

logger = logging.getLogger(__name__)

#: Exact format selector requested by the product spec: prefer a separate
#: mp4 video + m4a audio pair (merged into an mp4 container), then any single
#: mp4, then anything else as a last resort.
FORMAT_SELECTOR = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

#: Extensions that count as the actual downloaded media file (i.e. anything
#: that is not a thumbnail or a partial ``.part`` download).
MEDIA_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}

#: Extensions yt-dlp may write for a thumbnail. ``convert_thumbnails='jpg'``
#: normally leaves a ``.jpg`` behind, but a failed conversion could leave the
#: original format instead.
THUMBNAIL_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

_ANSI_RE = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|[@-Z\\-_])")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences (colors, cursor moves) from *text*."""
    return _ANSI_RE.sub("", text or "")


def short_error(exc: BaseException) -> str:
    """One-line, ANSI-free, <=300 character summary of *exc*.

    yt-dlp error strings are often multi-line and colored; task rows deserve
    a short, human-readable message instead.
    """
    message = strip_ansi(str(exc)).strip()
    first_line = message.splitlines()[0].strip() if message else ""
    if not first_line:
        first_line = type(exc).__name__
    return first_line[:300]


class DownloadResult(NamedTuple):
    """Successful outcome of :meth:`YtDlpDownloader.download`."""

    #: Path to the downloaded (and, when needed, merged) media file.
    path: Path
    #: yt-dlp info dict (``title``, ``id``, ...).
    info: dict
    #: Path to a thumbnail yt-dlp wrote, if any (may be a non-jpg format).
    thumbnail: Optional[Path]


class ProgressMapper:
    """Maps yt-dlp download-hook ticks onto the 5-90 percent range.

    yt-dlp invokes the hook with dicts such as::

        {"status": "downloading", "downloaded_bytes": 1024,
         "total_bytes": 4096, "tmpfilename": "/tmp/xyz.part"}
        {"status": "finished", "filename": "/tmp/xyz.mp4", ...}

    When video and audio are downloaded as separate formats the hook fires
    once per stream; each stream is tracked by its ``tmpfilename`` and the
    reported value is the mean completion over all streams seen so far, never
    moving backwards. Ticks without a known total simply do not move the
    needle (they must never crash the hook), and a misbehaving callback must
    never kill the download.
    """

    START = 5.0
    END = 90.0
    #: Minimum change (in percentage points) between two reports; keeps the
    #: database from being hammered once per chunk.
    MIN_STEP = 1.0

    def __init__(self, callback: Callable[[float], None]):
        self._callback = callback
        self._fractions: dict = {}
        self._reported: Optional[float] = None

    def __call__(self, status: dict) -> None:
        if not isinstance(status, dict):
            return
        state = status.get("status")
        key = status.get("tmpfilename") or status.get("filename")
        if not key:
            return
        if state == "finished":
            self._fractions[key] = 1.0
        elif state == "downloading":
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            downloaded = status.get("downloaded_bytes") or 0
            try:
                fraction = float(downloaded) / float(total)
            except (TypeError, ValueError, ZeroDivisionError):
                return  # unknown/invalid totals must not crash the hook
            self._fractions[key] = min(1.0, max(0.0, fraction))
        else:
            return
        self._report()

    def _report(self) -> None:
        if not self._fractions:
            return
        fraction = sum(self._fractions.values()) / len(self._fractions)
        percent = self.START + (self.END - self.START) * fraction
        # Throttle to whole-percent steps, but always let the final 90
        # through; never report a value lower than what was reported before.
        if self._reported is not None and percent < min(
            self.END, self._reported + self.MIN_STEP
        ):
            return
        self._reported = max(percent, self._reported or 0.0)
        try:
            self._callback(self._reported)
        except Exception:  # noqa: BLE001 - progress reporting is best-effort
            logger.exception("progress callback raised; ignoring")


class YtDlpDownloader:
    """Downloads media with yt-dlp (library API, no subprocess).

    All parameters have production defaults; tests may override them. Errors
    are re-raised as :class:`RuntimeError` with a short human-readable
    message so callers never need to import yt-dlp exception types.
    """

    def __init__(
        self,
        format_selector: str = FORMAT_SELECTOR,
        socket_timeout: int = 30,
        retries: int = 3,
    ):
        self.format_selector = format_selector
        self.socket_timeout = socket_timeout
        self.retries = retries

    # -- public API ------------------------------------------------------

    def probe(self, url: str) -> dict:
        """Return yt-dlp metadata for *url* without downloading anything."""
        opts = self._base_opts()
        opts["skip_download"] = True
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except DownloadError as exc:
            raise RuntimeError(short_error(exc)) from exc
        return self._single_entry(info, url)

    def download(
        self,
        url: str,
        staging_dir,
        progress_cb: Optional[Callable[[float], None]] = None,
    ) -> DownloadResult:
        """Download *url* into *staging_dir* in a single yt-dlp pass.

        ``progress_cb(percent)`` is called (throttled to whole percent steps)
        as the download advances from 5 to 90 percent. Returns the media
        path, the info dict and the thumbnail yt-dlp produced (``None`` when
        the source had no thumbnail).
        """
        staging = Path(staging_dir)
        staging.mkdir(parents=True, exist_ok=True)
        hook = ProgressMapper(progress_cb) if progress_cb else None

        opts = self._base_opts()
        opts.update(
            {
                "format": self.format_selector,
                "merge_output_format": "mp4",
                "outtmpl": str(staging / "%(id)s.%(ext)s"),
                "restrictfilenames": True,
                "writethumbnail": True,
                "convert_thumbnails": "jpg",
                "progress_hooks": [hook] if hook else [],
            }
        )
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except DownloadError as exc:
            raise RuntimeError(short_error(exc)) from exc

        info = self._single_entry(info, url)
        path = self._locate_media(staging, info)
        thumbnail = self._locate_thumbnail(staging)
        return DownloadResult(path=path, info=info, thumbnail=thumbnail)

    # -- internals ---------------------------------------------------------

    def _base_opts(self) -> dict:
        return {
            "quiet": True,
            "no_warnings": True,
            # quiet alone still prints [download] progress lines in current
            # yt-dlp; workers must stay silent (progress_hooks are unaffected).
            "noprogress": True,
            "noplaylist": True,
            "socket_timeout": self.socket_timeout,
            "retries": self.retries,
            # Keep yt-dlp from writing a cache directory to $HOME.
            "cachedir": False,
        }

    @staticmethod
    def _single_entry(info, url: str) -> dict:
        """Deflate playlist results to their first entry."""
        if not isinstance(info, dict):
            raise RuntimeError(f"yt-dlp returned no metadata for {url}")
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if isinstance(e, dict)]
            if not entries:
                raise RuntimeError(f"playlist at {url} contains no downloadable videos")
            info = entries[0]
        return info

    @staticmethod
    def _locate_media(staging: Path, info: dict) -> Path:
        """Find the downloaded media file inside *staging*.

        Preference order: the final path yt-dlp records on the info dict
        (set for plain downloads and for ffmpeg merges alike), then the
        per-format ``requested_downloads`` paths, then the largest media
        file in the staging directory (intermediate merge parts are already
        deleted by the time download() returns).
        """
        candidates = [info.get("filepath")]
        for entry in info.get("requested_downloads") or []:
            candidates.append(entry.get("filepath"))
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return Path(candidate)
        media_files = [
            p
            for p in staging.iterdir()
            if p.is_file() and p.suffix.lower() in MEDIA_EXTS
        ]
        if media_files:
            return max(media_files, key=lambda p: p.stat().st_size)
        raise RuntimeError(
            "yt-dlp finished for "
            f"{info.get('id') or info.get('title') or 'media'} "
            "but no downloaded file was found"
        )

    @staticmethod
    def _locate_thumbnail(staging: Path) -> Optional[Path]:
        """Find a thumbnail file inside *staging*, preferring jpg."""
        found = [
            p
            for p in staging.iterdir()
            if p.is_file() and p.suffix.lower() in THUMBNAIL_EXTS
        ]
        for p in found:
            if p.suffix.lower() in (".jpg", ".jpeg"):
                return p
        return max(found, key=lambda p: p.stat().st_size) if found else None
