"""Tests for the clip-studio app (v1.2).

Preset CRUD, clip CRUD + reorder, stream-info and download-section with
mocked subprocess calls (fully offline), serializer validation and the
font registry. One optional live-network test covers stream-info against
a real URL and is skipped unless VRS_LIVE_TESTS=1.
"""
import io
import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from django.core.files.base import ContentFile
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from rest_framework.test import APIClient, APITestCase

from apps.core import runner
from apps.core.models import Task
from apps.core.tests import wait_for_task
from apps.studio import fonts
from apps.studio.models import Clip, StylePreset

STREAM_MODULE = "apps.videos.services.stream"
SECTIONS_MODULE = "apps.videos.services.sections"

CLIP_FIELDS = {
    "id",
    "source_url",
    "title",
    "subtitle",
    "rank",
    "start_time",
    "end_time",
    "stream_url",
    "stream_type",
    "thumbnail",
    "duration",
    "style",
    "volume",
    "hd_status",
    "hd_file_url",
    "created_at",
}

PRESET_FIELDS = {"id", "name", "data", "created_at", "updated_at"}

LIVE_TESTS = os.environ.get("VRS_LIVE_TESTS") == "1"


def make_preset(name="Golden Bold", data=None):
    return StylePreset.objects.create(
        name=name, data=data or {"font": "Archivo Black", "fontSize": 64}
    )


def make_clip(**overrides):
    defaults = dict(
        source_url="https://example.com/watch?v=abc",
        title="Cat vs cucumber",
        subtitle="Round 1",
        rank=1,
        start_time=5.0,
        end_time=20.0,
        stream_url="https://cdn.example.com/preview.mp4",
        stream_type="mp4",
        thumbnail="https://example.com/thumb.jpg",
        duration=15.0,
        style={"font": "Bebas Neue"},
        volume=1.0,
    )
    defaults.update(overrides)
    return Clip.objects.create(**defaults)


# ---------------------------------------------------------------- presets --


class PresetCrudTests(APITestCase):
    """GET/POST /api/presets/, GET/PUT/DELETE /api/presets/<id>/."""

    def test_list_empty(self):
        response = self.client.get(reverse("preset-list"), format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"presets": []})

    def test_create_returns_201_and_shape(self):
        payload = {"name": "Neon", "data": {"font": "Rubik Bold", "fontSize": 72}}
        response = self.client.post(
            reverse("preset-list"), payload, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(response.data), PRESET_FIELDS)
        self.assertEqual(response.data["name"], "Neon")
        self.assertEqual(response.data["data"]["fontSize"], 72)
        self.assertTrue(StylePreset.objects.filter(name="Neon").exists())

    def test_create_rejects_duplicate_name(self):
        make_preset(name="Neon")
        response = self.client.post(
            reverse("preset-list"), {"name": "Neon", "data": {}}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.data)

    def test_create_rejects_blank_name(self):
        response = self.client.post(
            reverse("preset-list"), {"name": "   ", "data": {}}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_create_rejects_non_object_data(self):
        response = self.client.post(
            reverse("preset-list"),
            {"name": "Broken", "data": ["not", "a", "dict"]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("data", response.data)

    def test_list_orders_by_name(self):
        make_preset(name="Zeta")
        make_preset(name="Alpha")
        response = self.client.get(reverse("preset-list"), format="json")
        self.assertEqual(
            [p["name"] for p in response.data["presets"]], ["Alpha", "Zeta"]
        )

    def test_detail_get(self):
        preset = make_preset()
        response = self.client.get(
            reverse("preset-detail", args=[preset.id]), format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["name"], preset.name)

    def test_detail_unknown_404(self):
        response = self.client.get(reverse("preset-detail", args=[9999]), format="json")
        self.assertEqual(response.status_code, 404)

    def test_put_updates_fields(self):
        preset = make_preset()
        payload = {"name": "Renamed", "data": {"font": "Vazirmatn Bold"}}
        response = self.client.put(
            reverse("preset-detail", args=[preset.id]), payload, format="json"
        )
        self.assertEqual(response.status_code, 200)
        preset.refresh_from_db()
        self.assertEqual(preset.name, "Renamed")
        self.assertEqual(preset.data, {"font": "Vazirmatn Bold"})

    def test_delete(self):
        preset = make_preset()
        response = self.client.delete(
            reverse("preset-detail", args=[preset.id]), format="json"
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(StylePreset.objects.filter(pk=preset.id).exists())


# ------------------------------------------------------------------ clips --


class ClipCrudTests(APITestCase):
    """GET/POST /api/clips/, GET/PATCH/DELETE /api/clips/<id>/."""

    def test_list_empty(self):
        response = self.client.get(reverse("clip-list"), format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"clips": []})

    def test_create_returns_201_and_shape(self):
        payload = {
            "source_url": "https://example.com/watch?v=xyz",
            "title": "Skate fail",
            "rank": 3,
            "start_time": 10.5,
            "end_time": 25.0,
            "volume": 0.8,
            "style": {"font": "Rubik Bold", "fontSize": 48},
        }
        response = self.client.post(reverse("clip-list"), payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(response.data), CLIP_FIELDS)
        self.assertEqual(response.data["rank"], 3)
        self.assertEqual(response.data["hd_status"], "pending")
        self.assertEqual(response.data["stream_type"], "none")
        self.assertIsNone(response.data["hd_file_url"])
        clip = Clip.objects.get(pk=response.data["id"])
        self.assertEqual(clip.volume, 0.8)

    def test_list_orders_by_rank(self):
        second = make_clip(rank=2, title="B")
        first = make_clip(rank=1, title="A")
        response = self.client.get(reverse("clip-list"), format="json")
        clips = response.data["clips"]
        self.assertEqual([c["rank"] for c in clips], [1, 2])
        self.assertEqual(clips[0]["title"], "A")

    def test_detail_get(self):
        clip = make_clip()
        response = self.client.get(
            reverse("clip-detail", args=[clip.pk]), format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), CLIP_FIELDS)
        self.assertEqual(response.data["id"], str(clip.pk))

    def test_detail_unknown_404(self):
        response = self.client.get(
            reverse("clip-detail", args=[uuid.uuid4()]), format="json"
        )
        self.assertEqual(response.status_code, 404)

    def test_patch_partial_update(self):
        clip = make_clip(title="Before")
        response = self.client.patch(
            reverse("clip-detail", args=[clip.pk]),
            {"title": "After", "volume": 1.5},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        clip.refresh_from_db()
        self.assertEqual(clip.title, "After")
        self.assertEqual(clip.volume, 1.5)

    def test_delete_removes_row(self):
        clip = make_clip()
        response = self.client.delete(
            reverse("clip-detail", args=[clip.pk]), format="json"
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Clip.objects.filter(pk=clip.pk).exists())

    def test_delete_removes_hd_file(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name)
        override.enable()
        self.addCleanup(override.disable)

        clip = make_clip()
        clip.hd_file.save("hd.mp4", ContentFile(b"fake-hd"), save=True)
        hd_path = Path(clip.hd_file.path)
        self.assertTrue(hd_path.exists())

        response = self.client.delete(
            reverse("clip-detail", args=[clip.pk]), format="json"
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(hd_path.exists())


class ClipReorderTests(APITestCase):
    """POST /api/clips/reorder/."""

    def test_reorder_updates_ranks(self):
        a = make_clip(rank=1, title="A")
        b = make_clip(rank=2, title="B")
        c = make_clip(rank=3, title="C")

        response = self.client.post(
            reverse("clip-reorder"),
            {"ids": [str(c.pk), str(a.pk), str(b.pk)]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        a.refresh_from_db()
        b.refresh_from_db()
        c.refresh_from_db()
        self.assertEqual((a.rank, b.rank, c.rank), (2, 3, 1))
        # The response echoes the new ordering.
        self.assertEqual(
            [cl["title"] for cl in response.data["clips"]], ["C", "A", "B"]
        )

    def test_reorder_unknown_id_400(self):
        make_clip(rank=1)
        response = self.client.post(
            reverse("clip-reorder"),
            {"ids": [str(uuid.uuid4())]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("unknown clip ids", response.data["error"])

    def test_reorder_rejects_duplicates(self):
        clip = make_clip(rank=1)
        response = self.client.post(
            reverse("clip-reorder"),
            {"ids": [str(clip.pk), str(clip.pk)]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("duplicates", str(response.data))

    def test_reorder_rejects_empty_and_missing(self):
        for payload in ({}, {"ids": []}):
            response = self.client.post(
                reverse("clip-reorder"), payload, format="json"
            )
            self.assertEqual(response.status_code, 400)


# --------------------------------------------------------- clip validation --


class ClipValidationTests(APITestCase):
    """Serializer validation rules from the v1.2 spec."""

    def valid_payload(self, **overrides):
        payload = {
            "source_url": "https://example.com/watch?v=v",
            "rank": 1,
            "start_time": 1.0,
            "end_time": 10.0,
        }
        payload.update(overrides)
        return payload

    def post(self, payload):
        return self.client.post(reverse("clip-list"), payload, format="json")

    def test_negative_start_time_rejected(self):
        response = self.post(self.valid_payload(start_time=-0.5))
        self.assertEqual(response.status_code, 400)
        self.assertIn("start_time", response.data)

    def test_end_before_start_rejected(self):
        response = self.post(self.valid_payload(start_time=10.0, end_time=5.0))
        self.assertEqual(response.status_code, 400)

    def test_end_equal_start_rejected(self):
        response = self.post(self.valid_payload(start_time=5.0, end_time=5.0))
        self.assertEqual(response.status_code, 400)

    def test_less_than_1s_gap_rejected(self):
        response = self.post(self.valid_payload(start_time=5.0, end_time=5.5))
        self.assertEqual(response.status_code, 400)
        self.assertIn("1 second", str(response.data))

    def test_exactly_1s_gap_accepted(self):
        response = self.post(self.valid_payload(start_time=5.0, end_time=6.0))
        self.assertEqual(response.status_code, 201)

    def test_zero_rank_rejected(self):
        response = self.post(self.valid_payload(rank=0))
        self.assertEqual(response.status_code, 400)
        self.assertIn("rank", response.data)

    def test_negative_rank_rejected(self):
        response = self.post(self.valid_payload(rank=-3))
        self.assertEqual(response.status_code, 400)

    def test_volume_above_2_rejected(self):
        response = self.post(self.valid_payload(volume=2.5))
        self.assertEqual(response.status_code, 400)
        self.assertIn("volume", response.data)

    def test_negative_volume_rejected(self):
        response = self.post(self.valid_payload(volume=-0.1))
        self.assertEqual(response.status_code, 400)

    def test_volume_bounds_accepted(self):
        self.assertEqual(self.post(self.valid_payload(volume=0.0)).status_code, 201)
        self.assertEqual(self.post(self.valid_payload(volume=2.0)).status_code, 201)

    def test_bad_source_url_rejected(self):
        response = self.post(self.valid_payload(source_url="not-a-url"))
        self.assertEqual(response.status_code, 400)

    def test_missing_required_fields_rejected(self):
        response = self.client.post(reverse("clip-list"), {}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_non_object_style_rejected(self):
        response = self.post(self.valid_payload(style=[1, 2]))
        self.assertEqual(response.status_code, 400)

    def test_patch_may_update_single_bound(self):
        clip = make_clip(start_time=5.0, end_time=20.0)
        response = self.client.patch(
            reverse("clip-detail", args=[clip.pk]),
            {"end_time": 30.0},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

    def test_patch_validates_against_current_values(self):
        """PATCHing only end_time still checks it against the stored start."""
        clip = make_clip(start_time=5.0, end_time=20.0)
        response = self.client.patch(
            reverse("clip-detail", args=[clip.pk]),
            {"end_time": 4.0},
            format="json",
        )
        self.assertEqual(response.status_code, 400)


# ------------------------------------------------------------ stream-info --


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class StreamInfoTests(APITestCase):
    """POST /api/stream-info/ with mocked subprocess (offline)."""

    def post(self, payload):
        return self.client.post(
            reverse("stream-info"), payload, format="json"
        )

    def _mock_ytdlp(self, metadata_json=None, stream_stdout=""):
        """Patch subprocess.run so the metadata call and the -g call succeed."""
        metadata_json = metadata_json or json.dumps(
            {
                "title": "Test video",
                "duration": 187.5,
                "thumbnail": "https://example.com/t.jpg",
            }
        )

        def fake_run(cmd, **kwargs):
            if "-j" in cmd:
                return FakeCompleted(stdout=metadata_json)
            return FakeCompleted(stdout=stream_stdout)

        return mock.patch(f"{STREAM_MODULE}.subprocess.run", side_effect=fake_run)

    def test_success_returns_metadata_and_stream(self):
        with self._mock_ytdlp(
            stream_stdout="https://cdn.example.com/video.mp4?tok=1\n"
        ):
            response = self.post({"url": "https://youtube.com/watch?v=abc"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {
                "title": "Test video",
                "duration": 187.5,
                "thumbnail": "https://example.com/t.jpg",
                "stream_url": "https://cdn.example.com/video.mp4?tok=1",
                "stream_type": "mp4",
                "audio_url": None,
            },
        )

    def test_merge_fallback_returns_video_and_audio_urls(self):
        """Modern YouTube serves no progressive formats: the primary selector
        fails and the bestvideo*+bestaudio fallback yields two urls."""

        def fake_run(cmd, **kwargs):
            if "-j" in cmd:
                return FakeCompleted(
                    stdout=json.dumps({"title": "T", "duration": 12.0})
                )
            selector = cmd[cmd.index("-f") + 1]
            if "bestvideo*" in selector:
                return FakeCompleted(
                    stdout=(
                        "https://cdn.example.com/video-only.mp4\n"
                        "https://cdn.example.com/audio-only.webm\n"
                    )
                )
            return FakeCompleted(returncode=1, stderr="Requested format is not available")

        with mock.patch(
            f"{STREAM_MODULE}.subprocess.run", side_effect=fake_run
        ):
            response = self.post({"url": "https://youtube.com/watch?v=abc"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["stream_type"], "mp4")
        self.assertEqual(
            response.data["stream_url"],
            "https://cdn.example.com/video-only.mp4",
        )
        self.assertEqual(
            response.data["audio_url"],
            "https://cdn.example.com/audio-only.webm",
        )

    def test_hls_stream_classified(self):
        with self._mock_ytdlp(
            stream_stdout="https://cdn.example.com/master.m3u8\n"
        ):
            response = self.post({"url": "https://youtube.com/watch?v=abc"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["stream_type"], "hls")

    def test_stream_failure_degrades_to_none(self):
        """Both selectors failing still returns metadata with stream_type none."""

        def fake_run(cmd, **kwargs):
            if "-j" in cmd:
                return FakeCompleted(
                    stdout=json.dumps({"title": "T", "duration": 10.0})
                )
            return FakeCompleted(returncode=1, stderr="no formats found")

        with mock.patch(
            f"{STREAM_MODULE}.subprocess.run", side_effect=fake_run
        ):
            response = self.post({"url": "https://youtube.com/watch?v=abc"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["stream_type"], "none")
        self.assertIsNone(response.data["stream_url"])
        self.assertIsNone(response.data["audio_url"])
        self.assertEqual(response.data["title"], "T")

    def test_metadata_failure_returns_502(self):
        with mock.patch(
            f"{STREAM_MODULE}.subprocess.run",
            return_value=FakeCompleted(returncode=1, stderr="ERROR: not found"),
        ):
            response = self.post({"url": "https://youtube.com/watch?v=abc"})
        self.assertEqual(response.status_code, 502)
        self.assertIn("error", response.data)

    def test_invalid_url_400(self):
        for payload in ({}, {"url": ""}, {"url": "junk"}, {"url": 42}):
            response = self.post(payload)
            self.assertEqual(response.status_code, 400, payload)

    def test_playlist_metadata_deflated_to_first_entry(self):
        meta = json.dumps(
            {
                "_type": "playlist",
                "entries": [{"title": "Entry 0", "duration": 5.0}],
            }
        )
        with self._mock_ytdlp(
            metadata_json=meta, stream_stdout="https://x/v.mp4\n"
        ):
            response = self.post({"url": "https://youtube.com/playlist?l=1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["title"], "Entry 0")

    def test_command_includes_ua_and_timeout(self):
        """The skill-mandated flags must be present in both yt-dlp calls."""
        seen = []

        def fake_run(cmd, **kwargs):
            seen.append(cmd)
            if "-j" in cmd:
                return FakeCompleted(stdout=json.dumps({"title": "T"}))
            return FakeCompleted(stdout="https://x/v.mp4\n")

        with mock.patch(
            f"{STREAM_MODULE}.subprocess.run", side_effect=fake_run
        ):
            self.post({"url": "https://youtube.com/watch?v=abc"})
        self.assertEqual(len(seen), 2)
        for cmd in seen:
            self.assertIn("--user-agent", cmd)
            self.assertIn("--socket-timeout", cmd)
            self.assertIn("--retries", cmd)


@unittest.skipUnless(
    os.environ.get("VRS_LIVE_TESTS") == "1",
    "live network test; set VRS_LIVE_TESTS=1 to enable",
)
class StreamInfoLiveTests(APITestCase):
    """One optional live-network test (off by default)."""

    def test_live_stream_info(self):
        import urllib.request

        try:
            urllib.request.urlopen("https://www.youtube.com", timeout=5)
        except OSError:
            self.skipTest("no network access")

        from apps.videos.services.stream import get_stream_info

        info = get_stream_info("https://www.youtube.com/watch?v=aqz-KE-bpKQ")
        self.assertIn(info["stream_type"], ("mp4", "hls", "none"))
        self.assertTrue(info["title"])


# ------------------------------------------------------- download-section --


class DownloadSectionTests(TransactionTestCase):
    """POST /api/download-section/ with the whole pipeline mocked (offline).

    TransactionTestCase: the runner's worker thread owns its own DB
    connection and can only see committed rows.
    """

    client_class = APIClient

    def setUp(self):
        super().setUp()
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name)
        override.enable()
        self.addCleanup(override.disable)
        self.media_root = media.name

    def _stub_environment(self, section_ok=True, fallback_ok=True):
        """Install fake subprocess layers for the sections module.

        Returns a dict of call logs the test can inspect. The fake yt-dlp
        writes a real (tiny) mp4 file so the pipeline can complete without
        ffmpeg; ffprobe is also stubbed.
        """
        calls = {"section": [], "full": [], "ffmpeg": [], "probe": []}

        class FakeProc:
            """Minimal stand-in for subprocess.Popen with file-like streams."""

            def __init__(self, cmd, ok=True, stderr_text=""):
                self.cmd = cmd
                self.returncode = 0 if ok else 1
                self.stdout = io.StringIO("")
                self.stderr = io.StringIO(stderr_text)

            def wait(self, timeout=None):
                return self.returncode

            def kill(self):
                pass

        def fake_popen(cmd, **kwargs):
            if "--download-sections" in cmd:
                calls["section"].append(cmd)
                # Locate the -o template and materialize the output file.
                out = Path(cmd[cmd.index("-o") + 1])
                target = out.parent / "section.mp4"
                if section_ok:
                    target.write_bytes(b"fake-section-mp4")
                    return FakeProc(cmd, ok=True)
                return FakeProc(
                    cmd, ok=False, stderr_text="ERROR: section download rejected"
                )
            calls["full"].append(cmd)
            out = Path(cmd[cmd.index("-o") + 1])
            if fallback_ok:
                (out.parent / "full.mp4").write_bytes(b"fake-full-mp4")
                return FakeProc(cmd, ok=True)
            return FakeProc(
                cmd, ok=False, stderr_text="ERROR: download rejected"
            )

        def fake_run(cmd, **kwargs):
            if cmd[0] == "ffprobe":
                calls["probe"].append(cmd)
                return FakeCompleted(
                    stdout=json.dumps(
                        {
                            "streams": [
                                {"codec_type": "video", "width": 1920, "height": 1080},
                                {"codec_type": "audio"},
                            ],
                            "format": {"duration": "14.9"},
                        }
                    )
                )
            if cmd[0] == "ffmpeg":
                calls["ffmpeg"].append(cmd)
                # Materialize the trim output (last argument).
                if fallback_ok:
                    Path(cmd[-1]).write_bytes(b"fake-trimmed-mp4")
                return FakeCompleted(returncode=0 if fallback_ok else 1)
            raise AssertionError(f"unexpected command: {cmd}")

        patchers = [
            mock.patch(f"{SECTIONS_MODULE}.subprocess.Popen", side_effect=fake_popen),
            mock.patch(
                "apps.videos.services.probe.subprocess.run",
                side_effect=fake_run,
            ),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        return calls

    def test_happy_path_marks_clip_ready(self):
        clip = make_clip(rank=1)
        calls = self._stub_environment(section_ok=True)

        response = self.client.post(
            reverse("download-section"),
            {"clip_id": str(clip.pk)},
            format="json",
        )
        self.assertEqual(response.status_code, 202)
        task_id = response.data["task_id"]

        done = wait_for_task(
            self.client, task_id, lambda r: r.data["status"] == Task.STATUS_SUCCESS
        )
        self.assertEqual(done.data["progress"], 100.0)
        self.assertEqual(done.data["result"]["hd_status"], "ready")

        clip.refresh_from_db()
        self.assertEqual(clip.hd_status, "ready")
        self.assertTrue(clip.hd_file.name.startswith("hd_clips/"))
        self.assertTrue(clip.hd_file.name.endswith(".mp4"))
        self.assertEqual(
            Path(clip.hd_file.path).read_bytes(), b"fake-section-mp4"
        )
        self.assertEqual(clip.duration, 14.9)

        # Primary path used --download-sections and never ffmpeg.
        self.assertEqual(len(calls["section"]), 1)
        section_cmd = calls["section"][0]
        self.assertIn("--force-keyframes-at-cuts", section_cmd)
        self.assertIn(
            "*00:00:05-00:00:20",
            section_cmd[section_cmd.index("--download-sections") + 1],
        )
        self.assertEqual(calls["ffmpeg"], [])

    def test_fallback_path_when_section_rejected(self):
        clip = make_clip(rank=1)
        calls = self._stub_environment(section_ok=False, fallback_ok=True)

        response = self.client.post(
            reverse("download-section"),
            {"clip_id": str(clip.pk)},
            format="json",
        )
        self.assertEqual(response.status_code, 202)
        task_id = response.data["task_id"]

        done = wait_for_task(
            self.client, task_id, lambda r: r.data["status"] == Task.STATUS_SUCCESS
        )
        self.assertEqual(done.data["result"]["hd_status"], "ready")

        clip.refresh_from_db()
        self.assertEqual(clip.hd_status, "ready")
        # The trimmed fallback output was stored.
        self.assertEqual(
            Path(clip.hd_file.path).read_bytes(), b"fake-trimmed-mp4"
        )
        # Both attempts happened and ffmpeg trimmed.
        self.assertEqual(len(calls["section"]), 1)
        self.assertEqual(len(calls["full"]), 1)
        self.assertEqual(len(calls["ffmpeg"]), 1)
        ffmpeg_cmd = calls["ffmpeg"][0]
        self.assertIn("-ss", ffmpeg_cmd)

    def test_total_failure_marks_clip_failed(self):
        clip = make_clip(rank=1)
        self._stub_environment(section_ok=False, fallback_ok=False)

        response = self.client.post(
            reverse("download-section"),
            {"clip_id": str(clip.pk)},
            format="json",
        )
        self.assertEqual(response.status_code, 202)
        task_id = response.data["task_id"]

        done = wait_for_task(
            self.client, task_id, lambda r: r.data["status"] == Task.STATUS_FAILED
        )
        self.assertTrue(done.data["error"])
        clip.refresh_from_db()
        self.assertEqual(clip.hd_status, "failed")
        self.assertFalse(clip.hd_file)

    def test_unknown_clip_404(self):
        response = self.client.post(
            reverse("download-section"),
            {"clip_id": str(uuid.uuid4())},
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_missing_clip_id_400(self):
        response = self.client.post(reverse("download-section"), {}, format="json")
        self.assertEqual(response.status_code, 400)


class SectionFormattingTests(TestCase):
    """Unit tests for the --download-sections timestamp formatter."""

    def test_simple(self):
        from apps.videos.services.sections import format_section

        self.assertEqual(format_section(5, 20), "*00:00:05-00:00:20")

    def test_minutes_and_hours(self):
        from apps.videos.services.sections import format_section

        self.assertEqual(
            format_section(65.0, 3725.0), "*00:01:05-01:02:05"
        )

    def test_negative_start_clamped(self):
        from apps.videos.services.sections import format_section

        self.assertEqual(format_section(-3, 2), "*00:00:00-00:00:02")


# ----------------------------------------------------------------- fonts --


class FontRegistryTests(TestCase):
    """Font directory resolution and the fonts.json registry."""

    def test_fonts_json_is_valid_and_complete(self):
        expected = {
            "Archivo Black": "ArchivoBlack.ttf",
            "Bebas Neue": "BebasNeue-Regular.ttf",
            "Rubik Bold": "Rubik-Bold.ttf",
            "Montserrat ExtraBold": "Montserrat-ExtraBold.ttf",
            "Vazirmatn Bold": "Vazirmatn-Bold.ttf",
        }
        self.assertEqual(fonts.FONT_MAP, expected)
        for friendly, filename in expected.items():
            self.assertEqual(fonts.FONT_MAP.get(friendly), filename)

    def test_font_dir_resolves(self):
        # Locally the repo layout must exist and contain fonts.json.
        self.assertEqual(fonts.FONT_DIR.name, "fonts")
        self.assertTrue((fonts.FONT_DIR / "fonts.json").is_file())

    def test_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ, {"VRS_FONT_DIR": str(tmp)}
            ):
                resolved = fonts.resolve_font_dir()
                self.assertEqual(resolved, Path(tmp))

    def test_font_path_found_when_file_exists(self):
        # At least one downloaded TTF must resolve through font_path().
        missing = [n for n in fonts.FONT_MAP if fonts.font_path(n) is None]
        downloaded = set(fonts.FONT_MAP) - set(missing)
        self.assertTrue(
            downloaded,
            "no font TTFs found on disk (expected the 5 downloaded TTFs)",
        )
