"""Partial HD section downloader for studio clips (v1.2).

``run_download_section`` is the callable handed to
:mod:`apps.core.runner` by ``POST /api/download-section/``. It downloads
ONLY the requested ``[start_time, end_time]`` slice of the clip's source
URL using yt-dlp's ``--download-sections`` (with
``--force-keyframes-at-cuts`` for frame-accurate cut points and an
mp4+m4a format selector that yt-dlp merges into a single mp4), probes the
result with ffprobe and moves it to ``media/hd_clips/<uuid>.mp4``.

Some sites reject section downloads outright; in that case the worker
FALLS BACK to a full download followed by an ffmpeg trim (the v1.1 path).
Progress is reported through the shared Task row (0-100).

Progress checkpoints:

     2    pipeline started
    10    section download began
    10-70 section download (yt-dlp progress parsed from stderr)
    40-85 fallback full download (same stderr parsing, offset range)
    88    ffmpeg trim (fallback path only)
    95    probe + move into media
    100   done
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from django.conf import settings

from apps.core.runner import set_progress
from apps.studio.models import Clip
from apps.videos.services.probe import ProbeError, probe_media
from apps.videos.services.stream import USER_AGENT, yt_dlp_command

logger = logging.getLogger(__name__)

#: Skill-approved format selector: separate mp4 video + m4a audio merged
#: into mp4, then any single mp4, then anything.
FORMAT_SELECTOR = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

#: Shared yt-dlp network options (skill: socket timeout + retries).
_NET_OPTS = ["--socket-timeout", "15", "--retries", "3"]

#: Staging area for in-flight section downloads, relative to MEDIA_ROOT.
STAGING_ROOT = Path("hd_clips") / ".staging"

_DOWNLOAD_TIMEOUT = 20 * 60  # generous: HD sources can be slow
_FFMPEG_TIMEOUT = 10 * 60

# Progress checkpoints (see module docstring).
P_STARTED = 2.0
P_DOWNLOAD_BEGIN = 10.0
P_DOWNLOAD_END = 70.0
P_FALLBACK_BEGIN = 40.0
P_FALLBACK_END = 85.0
P_TRIM = 88.0
P_FINALIZE = 95.0
P_DONE = 100.0

#: Matches ``[download]  42.3% of ...`` lines printed with --newline.
_PROGRESS_RE = re.compile(r"\[download\]\s+([0-9]{1,3}(?:\.[0-9]+)?)%")


def run_download_section(task, clip_id):
    """Materialize the HD section for a Clip; return a result payload.

    Runs on a worker thread with a freshly created Task. Marks the Clip
    ``hd_status`` as ``ready``/``failed`` along the way and raises
    ``RuntimeError`` with a short message on total failure.
    """
    set_progress(task.id, P_STARTED)
    clip = Clip.objects.get(pk=clip_id)

    staging = Path(settings.MEDIA_ROOT) / STAGING_ROOT / str(task.id)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        temp = staging / "section.%(ext)s"
        ok, detail = _download_section(clip, temp, task)
        if not ok:
            logger.info(
                "section download rejected for clip %s (%s); falling back "
                "to full download + trim",
                clip.pk,
                detail,
            )
            ok, detail = _download_and_trim(clip, staging, task)
        if not ok:
            clip.hd_status = Clip.HD_FAILED
            clip.save(update_fields=["hd_status"])
            raise RuntimeError(detail or "section download failed")

        media = _single_media_file(staging)
        facts = _probe(media)
        set_progress(task.id, P_FINALIZE)
        clip = _store(clip, media, facts)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            staging.parent.rmdir()
        except OSError:
            pass

    set_progress(task.id, P_DONE)
    return {
        "clip_id": str(clip.pk),
        "hd_status": clip.hd_status,
        "hd_file_url": clip.hd_file.url if clip.hd_file else None,
        "media_duration": facts["duration"],
        "width": facts["width"],
        "height": facts["height"],
    }


# -- primary path -----------------------------------------------------------


def _download_section(clip, out_template, task):
    """Download only ``[start, end]`` of the source into *out_template*.

    Returns ``(ok, detail)``. ``detail`` explains the failure so the caller
    can decide (and log) the fallback reason.
    """
    cmd = [
        *yt_dlp_command(),
        "--download-sections",
        format_section(clip.start_time, clip.end_time),
        "--force-keyframes-at-cuts",
        "-f",
        FORMAT_SELECTOR,
        "--merge-output-format",
        "mp4",
        "--user-agent",
        USER_AGENT,
        *_NET_OPTS,
        "--no-playlist",
        "--newline",
        "-o",
        str(out_template),
        clip.source_url,
    ]
    return _run_download(
        cmd, task, from_pct=P_DOWNLOAD_BEGIN, to_pct=P_DOWNLOAD_END
    )


# -- fallback path ----------------------------------------------------------


def _download_and_trim(clip, staging, task):
    """Full download + ffmpeg trim fallback (v1.1 path).

    Returns ``(ok, detail)``.
    """
    full = staging / "full.%(ext)s"
    cmd = [
        *yt_dlp_command(),
        "-f",
        FORMAT_SELECTOR,
        "--merge-output-format",
        "mp4",
        "--user-agent",
        USER_AGENT,
        *_NET_OPTS,
        "--no-playlist",
        "--newline",
        "-o",
        str(full),
        clip.source_url,
    ]
    ok, detail = _run_download(
        cmd, task, from_pct=P_FALLBACK_BEGIN, to_pct=P_FALLBACK_END
    )
    if not ok:
        return False, f"fallback full download failed: {detail}"

    source = _single_media_file(staging, exclude_prefix="section")
    trimmed = staging / "section-trimmed.mp4"
    set_progress(task.id, P_TRIM)
    if not _ffmpeg_trim(source, clip.start_time, clip.end_time, trimmed):
        return False, "fallback ffmpeg trim failed"
    return True, None


def _ffmpeg_trim(source, start, end, target):
    """Re-encode only the ``[start, end]`` slice — accurate, not fast."""
    duration = max(0.1, end - start)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(source),
        "-t",
        f"{duration:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(target),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("ffmpeg trim failed: %s", exc)
        return False
    if proc.returncode != 0:
        lines = (proc.stderr or "").strip().splitlines()
        logger.warning(
            "ffmpeg trim failed (exit %s): %s",
            proc.returncode,
            lines[-1] if lines else "",
        )
        return False
    return target.is_file() and target.stat().st_size > 0


# -- shared download runner ---------------------------------------------------


def _run_download(cmd, task, from_pct, to_pct):
    """Run a yt-dlp download command, mapping its percent output to Task progress.

    Returns ``(ok, detail)``; ``detail`` is the last error line on failure.
    """
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        return False, f"failed to execute yt-dlp: {exc}"

    last_error_line = ""
    try:
        for line in process.stderr or []:
            line = line.rstrip("\n")
            if not line:
                continue
            match = _PROGRESS_RE.search(line)
            if match:
                fraction = min(100.0, float(match.group(1))) / 100.0
                set_progress(task.id, from_pct + (to_pct - from_pct) * fraction)
            else:
                last_error_line = line
        process.wait(timeout=_DOWNLOAD_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return False, "download timed out"
    finally:
        for stream in (process.stdout, process.stderr):
            if stream:
                try:
                    stream.close()
                except OSError:
                    pass

    if process.returncode != 0:
        return False, _short(last_error_line) or f"yt-dlp exited {process.returncode}"
    return True, None


# -- helpers ----------------------------------------------------------------


def format_section(start, end):
    """Format ``*HH:MM:SS-HH:MM:SS`` for ``--download-sections``."""
    return f"*{_ts(start)}-{_ts(end)}"


def _ts(seconds):
    """Seconds → ``HH:MM:SS(.mmm)`` timestamp."""
    seconds = max(0.0, float(seconds or 0))
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    stamp = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    if millis:
        stamp += f".{millis:03d}".rstrip("0")
    return stamp


def _single_media_file(staging, exclude_prefix=None):
    """Locate the downloaded media file inside *staging*.

    yt-dlp writes to ``<template stem>.<ext>``; after a successful run the
    merged mp4 is what remains. Files whose name starts with
    *exclude_prefix* are ignored (used by the fallback path to pick the
    full download rather than a partial section attempt).
    """
    media_exts = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
    candidates = [
        p
        for p in staging.iterdir()
        if p.is_file()
        and p.suffix.lower() in media_exts
        and not p.name.startswith(".")
        and not p.name.endswith(".part")
        and (exclude_prefix is None or not p.name.startswith(exclude_prefix))
    ]
    if not candidates:
        raise RuntimeError("yt-dlp finished but no downloaded file was found")
    return max(candidates, key=lambda p: p.stat().st_size)


def _probe(path):
    try:
        return probe_media(path)
    except ProbeError as exc:
        raise RuntimeError(f"failed to probe HD section {path.name}: {exc}") from exc


def _store(clip, media, facts):
    """Move the section file into media storage and update the Clip."""
    name = f"hd_clips/{clip.pk}.mp4"
    dest = Path(settings.MEDIA_ROOT) / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(media), str(dest))

    clip.hd_status = Clip.HD_READY
    clip.duration = facts["duration"]
    clip.hd_file.name = name
    clip.save(update_fields=["hd_status", "duration", "hd_file"])
    return clip


def _short(text):
    if not text:
        return ""
    lines = text.strip().splitlines()
    return lines[-1].strip()[:300] if lines else ""
