"""Tests for the video ingestion and library API.

Validation tests run under ``TestCase``. Anything that goes through the
real task runner (fetch happy path) uses ``TransactionTestCase`` because
worker threads own separate database connections and can only see
committed rows.
"""
import os
import sys
import tempfile
import time
import types

from django.core.files.base import ContentFile
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from rest_framework.test import APIClient, APITestCase

from apps.core import runner
from apps.core.models import Task
from apps.core.tests import wait_for_task
from apps.videos.models import Video

FETCH_MODULE = "apps.videos.services.fetch"

VIDEO_FIELDS = {
    "id",
    "title",
    "source_url",
    "duration",
    "width",
    "height",
    "has_audio",
    "thumbnail_url",
    "video_url",
    "created_at",
}


class FakeFetchModule:
    """Context manager that swaps ``apps.videos.services.fetch`` in sys.modules.

    The view imports ``run_fetch`` lazily from that module, so installing a
    stub module makes the tests hermetic regardless of whether the real
    downloader (a separate workstream) exists yet. Passing
    ``run_fetch=None`` installs a module *without* the attribute, which
    makes the view's import fail with ImportError (the 503 path).
    """

    def __init__(self, run_fetch=None):
        self._run_fetch = run_fetch
        self._saved = None

    def __enter__(self):
        self._saved = sys.modules.get(FETCH_MODULE)
        module = types.ModuleType(FETCH_MODULE)
        if self._run_fetch is not None:
            module.run_fetch = self._run_fetch
        sys.modules[FETCH_MODULE] = module
        return module

    def __exit__(self, *exc_info):
        if self._saved is not None:
            sys.modules[FETCH_MODULE] = self._saved
        else:
            sys.modules.pop(FETCH_MODULE, None)
        return False


def media_override(testcase):
    """Point MEDIA_ROOT at a per-test temp dir and clean it up afterwards."""
    media = tempfile.TemporaryDirectory()
    testcase.addCleanup(media.cleanup)
    override = override_settings(MEDIA_ROOT=media.name)
    override.enable()
    testcase.addCleanup(override.disable)
    return media.name


def make_video(source_url="https://example.com/watch?v=abc", title="Clip",
               with_thumb=True):
    """Create a Video row backed by real files in the current MEDIA_ROOT.

    ``FileField.save`` prefixes names with the field's ``upload_to``, so
    bare filenames are passed here on purpose.
    """
    video = Video(
        source_url=source_url,
        title=title,
        duration=12.5,
        width=1920,
        height=1080,
        has_audio=True,
    )
    video.file.save("clip.mp4", ContentFile(b"fake-video-bytes"), save=False)
    if with_thumb:
        video.thumbnail.save("clip.jpg", ContentFile(b"fake-thumb-bytes"), save=False)
    video.save()
    return video


class FetchValidationTests(APITestCase):
    """POST /api/fetch/ — input validation (no task may be created)."""

    def post(self, payload):
        return self.client.post(reverse("fetch"), payload, format="json")

    def test_missing_url(self):
        response = self.post({})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.data)

    def test_none_url(self):
        response = self.post({"url": None})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.data)

    def test_non_string_url(self):
        response = self.post({"url": 42})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.data)

    def test_junk_url(self):
        response = self.post({"url": "not-a-url"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.data)

    def test_non_http_scheme(self):
        response = self.post({"url": "ftp://example.com/video.mp4"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.data)

    def test_url_too_long(self):
        response = self.post({"url": "https://example.com/" + "a" * 2000})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.data)

    def test_scheme_only_url(self):
        response = self.post({"url": "https://"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.data)

    def test_validation_failures_create_no_tasks(self):
        for payload in ({}, {"url": ""}, {"url": "junk"}):
            self.post(payload)
        self.assertEqual(Task.objects.count(), 0)

    def test_fetch_service_unavailable(self):
        """A fetch module without run_fetch must yield 503, not a crash."""
        with FakeFetchModule(run_fetch=None):
            response = self.post({"url": "https://example.com/watch?v=abc"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["error"], "fetch service unavailable")


class FetchHappyPathTests(TransactionTestCase):
    """POST /api/fetch/ with a stubbed downloader end-to-end."""

    client_class = APIClient

    def setUp(self):
        super().setUp()
        self.media_root = media_override(self)

    def test_fetch_submits_task_and_returns_result(self):
        def stub_run_fetch(task, url):
            runner.set_progress(task.id, 50.0)
            video = make_video(source_url=url, title="Fetched clip")
            return {
                "video_id": video.id,
                "title": video.title,
                "video_url": video.file.url,
                "thumbnail_url": None,
            }

        with FakeFetchModule(run_fetch=stub_run_fetch):
            response = self.client.post(
                reverse("fetch"),
                {"url": "https://example.com/watch?v=abc"},
                format="json",
            )
            self.assertEqual(response.status_code, 202)
            task_id = response.data["task_id"]
            # Validate it parses as a UUID and matches the created row.
            self.assertEqual(str(Task.objects.get(pk=task_id).id), task_id)

            done = wait_for_task(
                self.client, task_id, lambda r: r.data["status"] == Task.STATUS_SUCCESS
            )

        self.assertEqual(done.data["task_type"], Task.TYPE_FETCH)
        self.assertEqual(done.data["progress"], 100.0)
        self.assertEqual(done.data["result"]["title"], "Fetched clip")
        self.assertTrue(done.data["result"]["video_url"].startswith("/media/"))
        self.assertEqual(done.data["error"], "")

        # The stub really created the video on the worker thread.
        video_id = done.data["result"]["video_id"]
        self.assertTrue(Video.objects.filter(pk=video_id).exists())
        listing = self.client.get(reverse("video-list"), format="json")
        self.assertIn(video_id, [v["id"] for v in listing.data["videos"]])

    def test_url_is_normalized_before_submission(self):
        """The view strips whitespace and accepts mixed-case schemes."""
        submitted = {}

        def stub(task, url):
            submitted["url"] = url
            return {}

        with FakeFetchModule(run_fetch=stub):
            response = self.client.post(
                reverse("fetch"),
                {"url": "  HTTPS://Example.COM/a.mp4  "},
                format="json",
            )
            self.assertEqual(response.status_code, 202)
            task_id = response.data["task_id"]
            # Drain the real worker so the stub has provably run.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                task = Task.objects.filter(pk=task_id).first()
                if task and task.status == Task.STATUS_SUCCESS:
                    break
                time.sleep(0.01)
            else:
                self.fail(f"task {task_id} never succeeded; url={submitted!r}")

        self.assertEqual(submitted["url"], "HTTPS://Example.COM/a.mp4")


class VideoListViewTests(TestCase):
    """GET /api/videos/"""

    def setUp(self):
        super().setUp()
        self.media_root = media_override(self)

    def test_empty_list(self):
        response = self.client.get(reverse("video-list"), format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"videos": []})

    def test_lists_videos_with_relative_media_urls(self):
        first = make_video(title="First")
        second = make_video(
            source_url="https://example.com/watch?v=def",
            title="Second",
            with_thumb=False,
        )

        response = self.client.get(reverse("video-list"), format="json")
        self.assertEqual(response.status_code, 200)
        videos = response.data["videos"]
        self.assertEqual({v["id"] for v in videos}, {first.id, second.id})
        for item in videos:
            self.assertEqual(set(item), VIDEO_FIELDS)
            self.assertTrue(item["video_url"].startswith("/media/videos/"))
        by_id = {v["id"]: v for v in videos}
        self.assertTrue(by_id[first.id]["thumbnail_url"].startswith("/media/thumbs/"))
        self.assertIsNone(by_id[second.id]["thumbnail_url"])
        self.assertEqual(by_id[first.id]["source_url"], first.source_url)
        self.assertEqual(by_id[first.id]["duration"], 12.5)
        self.assertEqual(by_id[first.id]["width"], 1920)
        self.assertEqual(by_id[first.id]["height"], 1080)
        self.assertTrue(by_id[first.id]["has_audio"])


class VideoDetailViewTests(TestCase):
    """GET/DELETE /api/videos/<id>/"""

    def setUp(self):
        super().setUp()
        self.media_root = media_override(self)

    def test_detail_shape(self):
        video = make_video()
        response = self.client.get(
            reverse("video-detail", args=[video.id]), format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), VIDEO_FIELDS)
        self.assertEqual(response.data["id"], video.id)
        self.assertEqual(response.data["title"], video.title)
        self.assertEqual(response.data["video_url"], "/media/videos/clip.mp4")
        self.assertEqual(response.data["thumbnail_url"], "/media/thumbs/clip.jpg")

    def test_unknown_video_returns_404(self):
        response = self.client.get(reverse("video-detail", args=[99999]), format="json")
        self.assertEqual(response.status_code, 404)

    def test_delete_removes_row_and_files(self):
        video = make_video()
        file_path = video.file.path
        thumb_path = video.thumbnail.path
        self.assertTrue(os.path.exists(file_path))
        self.assertTrue(os.path.exists(thumb_path))

        response = self.client.delete(
            reverse("video-detail", args=[video.id]), format="json"
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")

        self.assertFalse(Video.objects.filter(pk=video.id).exists())
        self.assertFalse(os.path.exists(file_path))
        self.assertFalse(os.path.exists(thumb_path))

    def test_delete_tolerates_missing_files(self):
        """Files removed out-of-band must not prevent row deletion."""
        video = make_video()
        os.remove(video.file.path)
        os.remove(video.thumbnail.path)

        response = self.client.delete(
            reverse("video-detail", args=[video.id]), format="json"
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Video.objects.filter(pk=video.id).exists())

    def test_delete_video_without_thumbnail(self):
        video = make_video(with_thumb=False)
        response = self.client.delete(
            reverse("video-detail", args=[video.id]), format="json"
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Video.objects.filter(pk=video.id).exists())

    def test_delete_unknown_video_returns_404(self):
        response = self.client.delete(
            reverse("video-detail", args=[99999]), format="json"
        )
        self.assertEqual(response.status_code, 404)
