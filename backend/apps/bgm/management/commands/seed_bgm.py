"""Seed BGM tracks from the read-only BGM_SOURCE_DIR mount.

Usage::

    python manage.py seed_bgm

Recursively scans ``settings.BGM_SOURCE_DIR`` for audio files (.mp3/.m4a/
.wav/.flac/.ogg/.aac, case-insensitive). Every readable file is copied to
``MEDIA_ROOT/bgm/<uuid><ext>`` and upserted into a :class:`BgmTrack` row
keyed by its humanized filename stem, so running the command repeatedly
never duplicates rows — it refreshes durations and replaces the stored copy
(deleting the previous file). Files that ffprobe cannot read are skipped
with a warning.

Note: ``apps.videos.services.probe.probe_media`` cannot be reused here as-is
because it requires a video stream and raises for audio-only files, while
BGM tracks are audio-only by definition. Duration probing therefore uses the
dedicated ffprobe invocation below (the ProbeError type is shared).
"""
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.bgm.models import BgmTrack
from apps.videos.services.probe import ProbeError

#: Audio extensions recognized by the seeder (matched case-insensitively).
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac"}

_NAME_SEPARATORS = re.compile(r"[-_.\\s]+")


def humanize_name(stem: str) -> str:
    """Turn a filename stem into a display name.

    ``"cool_lofi-beat.2"`` becomes ``"Cool Lofi Beat 2"``: separators are
    replaced with spaces, whitespace collapses and lower-case words are
    capitalised (words with intentional casing are left untouched).
    """
    words = [w for w in _NAME_SEPARATORS.split(stem.strip()) if w]
    if not words:
        return ""
    return " ".join(w if not w.islower() else w.capitalize() for w in words)[:200]


def probe_duration(path: Path) -> float:
    """Return the duration (seconds) of an audio file via ffprobe.

    Raises :class:`ProbeError` when ffprobe is unavailable, fails, or does
    not report a duration.
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe timed out while probing {path}") from exc
    except OSError as exc:
        raise ProbeError(f"failed to execute ffprobe: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()[:200]
        raise ProbeError(f"ffprobe failed for {path}: {detail}")
    try:
        return float((proc.stdout or "").strip().splitlines()[0])
    except (IndexError, ValueError) as exc:
        raise ProbeError(f"ffprobe returned no duration for {path}") from exc


class Command(BaseCommand):
    help = (
        "Import background-music tracks from BGM_SOURCE_DIR into "
        "MEDIA_ROOT/bgm (idempotent; keyed by humanized filename stem)."
    )

    def handle(self, *args, **options):
        source = Path(settings.BGM_SOURCE_DIR)
        if not source.is_dir():
            self.stderr.write(
                f"warning: BGM_SOURCE_DIR '{source}' does not exist; nothing to seed"
            )
            return

        audio_files = sorted(
            path
            for path in source.rglob("*")
            if path.is_file()
            and path.suffix.lower() in AUDIO_EXTENSIONS
            and not path.name.startswith(".")
        )
        added = updated = skipped = 0
        for path in audio_files:
            name = humanize_name(path.stem)
            if not name:
                self.stderr.write(f"warning: skipping {path}: no usable track name")
                skipped += 1
                continue
            try:
                duration = probe_duration(path)
            except ProbeError as exc:
                self.stderr.write(f"warning: skipping {path}: {exc}")
                skipped += 1
                continue
            if duration <= 0:
                self.stderr.write(f"warning: skipping {path}: duration is {duration}")
                skipped += 1
                continue

            previous = BgmTrack.objects.filter(name=name).only("file").first()
            previous_file = previous.file.name if previous and previous.file else None

            stored_name = self._store_copy(path)
            _, was_created = BgmTrack.objects.update_or_create(
                name=name,
                defaults={"file": stored_name, "duration": duration},
            )
            if was_created:
                added += 1
            else:
                updated += 1
                if previous_file and previous_file != stored_name:
                    self._delete_stored(previous_file)

            verb = "added" if was_created else "updated"
            self.stdout.write(f"  {verb}: {name} ({duration:.1f}s) <- {path.name}")

        total = BgmTrack.objects.count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(audio_files)} file(s) from {source}: "
                f"{added} added, {updated} updated, {skipped} skipped; "
                f"{total} BgmTrack row(s) in database"
            )
        )

    @staticmethod
    def _store_copy(path: Path) -> str:
        """Copy *path* into MEDIA_ROOT/bgm under a fresh uuid name."""
        stored_name = f"bgm/{uuid.uuid4()}{path.suffix.lower()}"
        destination = Path(settings.MEDIA_ROOT) / stored_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        return stored_name

    @staticmethod
    def _delete_stored(stored_name: str) -> None:
        """Remove a previously stored copy (best effort)."""
        try:
            (Path(settings.MEDIA_ROOT) / stored_name).unlink(missing_ok=True)
        except OSError:
            pass
