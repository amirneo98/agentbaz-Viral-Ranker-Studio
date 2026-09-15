"""Integration tests for the fetch pipeline and BGM seeding.

These exercise the real thing: an actual network download of a small stable
sample video, a real ffmpeg frame-grab thumbnail, real ffprobe calls and
real wav synthesis for the seeder. Only the yt-dlp *failure* path is mocked,
because that is the one path which must not depend on the network.

Lives in ``tests_services.py`` (not ``tests.py``) so it never collides with
the API workstream's tests.
"""
import shutil
import subprocess
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from apps.bgm.models import BgmTrack
from apps.core.models import Task
from apps.videos.models import Video
from apps.videos.services.downloader import ProgressMapper, YtDlpDownloader
from apps.videos.services.fetch import run_fetch
from apps.videos.services.probe import probe_media
from yt_dlp import YoutubeDL as YoutubeDLClass
from yt_dlp.utils import DownloadError

#: Small (~1 MB), stable, publicly served sample clip. A direct mp4 URL:
#: no extractor needed and no embedded thumbnail, so the ffmpeg fallback
#: thumbnail path is the one being exercised. The file is video-only, which
#: also covers the "no audio must still succeed" requirement.
SAMPLE_MP4_URL = (
    "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/"
    "Big_Buck_Bunny_360_10s_1MB.mp4"
)


class ProgressMapperTests(SimpleTestCase):
    """Unit tests for the download-progress -> 5-90 % mapping."""

    def test_maps_bytes_to_5_90_range(self):
        seen = []
        mapper = ProgressMapper(seen.append)
        mapper({"status": "downloading", "tmpfilename": "v", "downloaded_bytes": 0, "total_bytes": 1000})
        mapper({"status": "downloading", "tmpfilename": "v", "downloaded_bytes": 500, "total_bytes": 1000})
        mapper({"status": "finished", "tmpfilename": "v"})
        self.assertEqual(seen, [5.0, 47.5, 90.0])

    def test_ticks_without_total_do_not_crash(self):
        seen = []
        mapper = ProgressMapper(seen.append)
        mapper({"status": "downloading", "tmpfilename": "v", "downloaded_bytes": 12345})
        mapper({"status": "downloading", "tmpfilename": "v"})
        mapper({"status": "finished", "tmpfilename": "v"})
        self.assertEqual(seen[-1], 90.0)

    def test_broken_callback_is_swallowed(self):
        mapper = ProgressMapper(lambda pct: 1 / 0)
        mapper({"status": "downloading", "tmpfilename": "v", "downloaded_bytes": 1, "total_bytes": 2})
        # A raising callback must never propagate into yt-dlp's download loop.
        mapper({"status": "finished", "tmpfilename": "v"})

    def test_second_stream_does_not_regress_progress(self):
        seen = []
        mapper = ProgressMapper(seen.append)
        mapper({"status": "downloading", "tmpfilename": "video", "downloaded_bytes": 100, "total_bytes": 100})
        mapper({"status": "downloading", "tmpfilename": "audio", "downloaded_bytes": 0, "total_bytes": 100})
        mapper({"status": "finished", "tmpfilename": "audio"})
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(seen[-1], 90.0)


class RunFetchRealDownloadTests(TestCase):
    """End-to-end fetch of a real public sample video (network required)."""

    def setUp(self):
        self.media_root = Path(tempfile.mkdtemp(prefix="vrs-test-media-"))
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)

    def test_run_fetch_creates_video_with_thumbnail(self):
        task = Task.objects.create(
            task_type=Task.TYPE_FETCH, status=Task.STATUS_PROCESSING
        )
        with override_settings(MEDIA_ROOT=str(self.media_root)):
            result = run_fetch(task, SAMPLE_MP4_URL)

        # The Video row exists and points at real files.
        self.assertEqual(Video.objects.count(), 1)
        video = Video.objects.get(pk=result["video_id"])
        self.assertEqual(video.source_url, SAMPLE_MP4_URL)
        self.assertTrue(video.title)
        self.assertLessEqual(len(video.title), 500)

        stored_video = self.media_root / video.file.name
        self.assertTrue(video.file.name.startswith("videos/"))
        self.assertTrue(video.file.name.endswith(".mp4"))
        self.assertTrue(stored_video.is_file())
        self.assertGreater(stored_video.stat().st_size, 100_000)

        # Direct mp4 downloads carry no thumbnail, so the ffmpeg fallback
        # must have produced a real JPEG.
        self.assertIsNotNone(video.thumbnail)
        stored_thumb = self.media_root / video.thumbnail.name
        self.assertTrue(video.thumbnail.name.startswith("thumbs/"))
        self.assertTrue(video.thumbnail.name.endswith(".jpg"))
        self.assertTrue(stored_thumb.is_file())
        self.assertGreater(stored_thumb.stat().st_size, 0)
        self.assertEqual(stored_thumb.read_bytes()[:2], b"\xff\xd8")

        # Probing the stored file again agrees with the persisted metadata.
        facts = probe_media(stored_video)
        self.assertGreater(facts["duration"], 5.0)
        self.assertGreater(facts["width"], 0)
        self.assertGreater(facts["height"], 0)
        self.assertEqual(video.has_audio, facts["has_audio"])
        self.assertFalse(video.has_audio)  # sample clip is video-only
        self.assertAlmostEqual(video.duration, facts["duration"], delta=0.5)
        self.assertEqual(video.width, facts["width"])
        self.assertEqual(video.height, facts["height"])

        # The returned payload uses relative, same-origin URLs.
        self.assertEqual(result["video_url"], f"/media/{video.file.name}")
        self.assertEqual(result["thumbnail_url"], f"/media/{video.thumbnail.name}")
        self.assertEqual(result["title"], video.title)
        self.assertEqual(result["duration"], video.duration)
        self.assertEqual(result["width"], video.width)
        self.assertEqual(result["height"], video.height)
        self.assertEqual(result["has_audio"], video.has_audio)

        # Progress ran all the way and the staging area was cleaned up.
        task.refresh_from_db()
        self.assertEqual(task.progress, 100.0)
        staging = self.media_root / "videos" / ".staging" / str(task.id)
        self.assertFalse(staging.exists())

    def test_probe_returns_metadata_without_downloading(self):
        info = YtDlpDownloader().probe(SAMPLE_MP4_URL)
        self.assertTrue(str(info.get("title") or "").strip())
        self.assertEqual(info.get("ext"), "mp4")


class RunFetchErrorMappingTests(TestCase):
    """yt-dlp failures must surface as short, clean RuntimeError messages."""

    def test_download_error_is_mapped_to_runtime_error(self):
        media_root = Path(tempfile.mkdtemp(prefix="vrs-test-media-"))
        self.addCleanup(shutil.rmtree, media_root, ignore_errors=True)
        task = Task.objects.create(
            task_type=Task.TYPE_FETCH, status=Task.STATUS_PROCESSING
        )
        boom = DownloadError(
            "ERROR: [generic] fake failure \x1b[31mred text\x1b[0m\nsecond line ignored"
        )
        with override_settings(MEDIA_ROOT=str(media_root)):
            with mock.patch.object(YoutubeDLClass, "extract_info", side_effect=boom):
                with self.assertRaises(RuntimeError) as caught:
                    run_fetch(task, "https://example.com/video.mp4")

        message = str(caught.exception)
        self.assertIn("fake", message)
        self.assertNotIn("\n", message)
        self.assertNotIn("\x1b", message)
        self.assertLessEqual(len(message), 300)

        # Nothing was persisted and the staging area is gone.
        self.assertEqual(Video.objects.count(), 0)
        staging = media_root / "videos" / ".staging" / str(task.id)
        self.assertFalse(staging.exists())


class SeedBgmCommandTests(TestCase):
    """seed_bgm against real wav files synthesised with ffmpeg."""

    def setUp(self):
        self.source_dir = Path(tempfile.mkdtemp(prefix="vrs-test-bgm-src-"))
        self.media_root = Path(tempfile.mkdtemp(prefix="vrs-test-bgm-media-"))
        self.addCleanup(shutil.rmtree, self.source_dir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        self._synth_wav("Alpha Track.wav", frequency=440)
        self._synth_wav("beta_loop.wav", frequency=880)

    def _synth_wav(self, filename, frequency):
        out = self.source_dir / filename
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:duration=2",
                str(out),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertTrue(out.is_file(), f"ffmpeg failed to create {out}")
        self.assertGreater(out.stat().st_size, 0)

    def _run_seed(self):
        out, err = StringIO(), StringIO()
        with override_settings(
            BGM_SOURCE_DIR=str(self.source_dir), MEDIA_ROOT=str(self.media_root)
        ):
            call_command("seed_bgm", stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_seeds_tracks_with_durations(self):
        output, _ = self._run_seed()
        self.assertEqual(BgmTrack.objects.count(), 2)
        self.assertEqual(
            sorted(BgmTrack.objects.values_list("name", flat=True)),
            ["Alpha Track", "Beta Loop"],
        )
        for track in BgmTrack.objects.all():
            self.assertAlmostEqual(track.duration, 2.0, delta=0.5)
            self.assertTrue(track.file.name.startswith("bgm/"))
            stored = self.media_root / track.file.name
            self.assertTrue(stored.is_file())
            self.assertGreater(stored.stat().st_size, 0)
        self.assertEqual(len(list((self.media_root / "bgm").iterdir())), 2)
        self.assertIn("2 added", output)

    def test_seeding_is_idempotent(self):
        first, _ = self._run_seed()
        self.assertIn("2 added", first)
        second, _ = self._run_seed()
        self.assertIn("2 updated", second)
        self.assertEqual(BgmTrack.objects.count(), 2)
        # Fresh copies replaced the old ones; no orphaned files remain.
        self.assertEqual(len(list((self.media_root / "bgm").iterdir())), 2)

    def test_unreadable_files_are_skipped_with_warning(self):
        (self.source_dir / "broken.wav").write_bytes(b"definitely not audio data")
        (self.source_dir / "notes.txt").write_text("not audio")
        _, err = self._run_seed()
        self.assertEqual(BgmTrack.objects.count(), 2)
        self.assertIn("broken.wav", err)
