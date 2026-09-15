"""v1.4 tests: POST /api/render/urls/ + GET /api/render/urls/preview/.

Layout:

* Serializer validation matrix — every 400 case (TestCase, no network).
* Payload construction — aspect, per-clip style, trim window, master
  title, settings defaults.
* Worker behaviour — de-dup (one downloader call per unique URL), cache
  and Video-row reuse, payload rewrite, progress windows, staging cleanup
  (mocked downloader, real Task rows).
* Preview endpoint — cache hit/miss, 404 without download.
* Endpoint submission — 202 → SUCCESS status transitions with the
  downloader and pipeline mocked (hermetic, real runner thread).
* One real end-to-end render from a verified reachable public URL with
  TWO clips sharing that URL — ffprobe asserts 1080x1920 H.264 output
  and the job records encoder_used.
"""
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from rest_framework.test import APIClient, APITestCase

from apps.core.models import Task
from apps.core.tests import wait_for_task
from apps.renders.models import RenderJob
from apps.renders.serializers import (
    UrlRenderRequestSerializer,
    build_urls_payload,
)
from apps.renders.services import urls as urls_svc
from apps.videos.models import Video
from apps.videos.services.probe import probe_media

from .tests_pipeline import make_video, media_override, synth_sample

#: Verified reachable, small (~1 MB), h264+aac, 10 s, 640x360.
E2E_SOURCE_URL = (
    "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/"
    "Big_Buck_Bunny_360_10s_1MB.mp4"
)


def make_clip(url="https://example.com/v.mp4", rank=1, start=0.0, end=2.0,
              **extra):
    clip = {
        "source_url": url,
        "rank": rank,
        "start": start,
        "end": end,
        "title": f"Clip #{rank}",
    }
    clip.update(extra)
    return clip


def make_request(clips=None, **extra):
    request = {
        "master_title": "Top Clips",
        "aspect": "9:16",
        "clips": clips if clips is not None else [make_clip()],
    }
    request.update(extra)
    return request


def fake_download_result(path):
    return SimpleNamespace(path=Path(path), info={"id": "x"}, thumbnail=None)


# ---------------------------------------------------------------------------
# Serializer validation matrix
# ---------------------------------------------------------------------------

class UrlRenderSerializerTests(TestCase):
    def assertInvalid(self, payload, needle=None):
        serializer = UrlRenderRequestSerializer(data=payload)
        self.assertFalse(serializer.is_valid(), msg=str(serializer.data))
        if needle:
            self.assertIn(needle, str(serializer.errors))

    def test_minimal_request_valid(self):
        serializer = UrlRenderRequestSerializer(data=make_request())
        self.assertTrue(serializer.is_valid(), msg=str(serializer.errors))

    def test_full_request_valid(self):
        payload = make_request(
            clips=[make_clip(
                style={"font": "Rubik", "fontSize": 48},
                subtitle="sub",
                volume=0.5,
            )],
            settings={
                "video_scale_pct": 90,
                "background_blur": True,
                "blur_sigma": 30.0,
                "bgm_volume": 0.2,
                "ducking_threshold": 0.02,
                "ducking_ratio": 6,
            },
        )
        serializer = UrlRenderRequestSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), msg=str(serializer.errors))

    # ---- clips -----------------------------------------------------------

    def test_empty_clips_rejected(self):
        self.assertInvalid(make_request(clips=[]), "at least one clip")

    def test_missing_clips_rejected(self):
        self.assertInvalid({"master_title": "x"}, "clips")

    def test_blank_source_url_rejected(self):
        self.assertInvalid(
            make_request(clips=[make_clip(url="   ")]), "source_url"
        )

    def test_missing_source_url_rejected(self):
        clip = make_clip()
        del clip["source_url"]
        self.assertInvalid(make_request(clips=[clip]), "source_url")

    def test_non_http_source_url_rejected(self):
        self.assertInvalid(
            make_request(clips=[make_clip(url="ftp://example.com/v.mp4")]),
            "http",
        )

    def test_relative_source_url_rejected(self):
        self.assertInvalid(
            make_request(clips=[make_clip(url="/media/videos/x.mp4")]),
            "http",
        )

    def test_bare_hostname_rejected(self):
        self.assertInvalid(
            make_request(clips=[make_clip(url="example.com/v.mp4")]),
            "http",
        )

    def test_end_equal_start_rejected(self):
        self.assertInvalid(
            make_request(clips=[make_clip(start=2.0, end=2.0)]), "greater"
        )

    def test_end_before_start_rejected(self):
        self.assertInvalid(
            make_request(clips=[make_clip(start=3.0, end=1.0)]), "greater"
        )

    def test_missing_start_rejected(self):
        clip = make_clip()
        del clip["start"]
        self.assertInvalid(make_request(clips=[clip]), "start")

    def test_missing_end_rejected(self):
        clip = make_clip()
        del clip["end"]
        self.assertInvalid(make_request(clips=[clip]), "start")

    def test_duplicate_ranks_rejected(self):
        self.assertInvalid(
            make_request(clips=[
                make_clip(rank=1), make_clip(rank=1, url="https://a.b/2.mp4"),
            ]),
            "unique",
        )

    # ---- aspect / settings ------------------------------------------------

    def test_invalid_aspect_rejected(self):
        self.assertInvalid(make_request(aspect="4:3"), "aspect")

    def test_unknown_bgm_rejected(self):
        self.assertInvalid(make_request(settings={"bgm_id": 99999}), "bgm")

    def test_out_of_range_settings_rejected(self):
        self.assertInvalid(
            make_request(settings={"video_scale_pct": 10}), "video_scale_pct"
        )
        self.assertInvalid(
            make_request(settings={"bgm_volume": 2.0}), "bgm_volume"
        )
        self.assertInvalid(
            make_request(settings={"ducking_ratio": 0.5}), "ducking_ratio"
        )


# ---------------------------------------------------------------------------
# Payload construction
# ---------------------------------------------------------------------------

class BuildUrlsPayloadTests(TestCase):
    def _validated(self, payload):
        serializer = UrlRenderRequestSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def test_defaults(self):
        out = build_urls_payload(self._validated(make_request()))
        self.assertEqual(out["master_title"], "Top Clips")
        self.assertEqual(out["aspect"], "9:16")
        self.assertEqual(
            out["settings"],
            {"video_height_pct": 80, "background_blur": True,
             "bgm_id": None, "bgm_volume": 0.4},
        )

    def test_settings_passthrough(self):
        payload = make_request(settings={
            "video_scale_pct": 90, "blur_sigma": 30.0,
            "ducking_threshold": 0.02, "ducking_ratio": 6,
            "bgm_volume": 0.25, "background_blur": False,
        })
        out = build_urls_payload(self._validated(payload))
        self.assertEqual(out["settings"]["video_height_pct"], 90)
        self.assertEqual(out["settings"]["video_scale_pct"], 90)
        self.assertFalse(out["settings"]["background_blur"])
        self.assertEqual(out["settings"]["blur_sigma"], 30.0)
        self.assertEqual(out["settings"]["ducking_threshold"], 0.02)
        self.assertEqual(out["settings"]["ducking_ratio"], 6.0)
        self.assertEqual(out["settings"]["bgm_volume"], 0.25)

    def test_aspect_16_9(self):
        out = build_urls_payload(self._validated(make_request(aspect="16:9")))
        self.assertEqual(out["aspect"], "16:9")

    def test_clip_shape(self):
        style = {"font": "Rubik", "fontSize": 48, "textColor": "#ffcc00"}
        clip = make_clip(style=style, subtitle="the sub", volume=0.5,
                         start=1.5, end=4.0)
        out = build_urls_payload(
            self._validated(make_request(clips=[clip]))
        )
        self.assertEqual(len(out["clips"]), 1)
        stored = out["clips"][0]
        self.assertEqual(stored["source_url"], clip["source_url"])
        self.assertEqual(stored["start"], 1.5)
        self.assertEqual(stored["end"], 4.0)
        self.assertEqual(stored["style"], style)
        self.assertEqual(stored["subtitle"], "the sub")
        self.assertEqual(stored["volume"], 0.5)
        self.assertEqual(stored["rank"], 1)
        self.assertNotIn("video_id", stored)

    def test_title_truncated(self):
        clip = make_clip(title="x" * 300)
        out = build_urls_payload(
            self._validated(make_request(clips=[clip]))
        )
        self.assertEqual(len(out["clips"][0]["title"]), 60)


# ---------------------------------------------------------------------------
# Endpoint validation (400s create nothing)
# ---------------------------------------------------------------------------

class UrlRenderValidationApiTests(APITestCase):
    def post(self, payload):
        return self.client.post(reverse("render-urls"), payload, format="json")

    def test_valid_request_returns_202_and_creates_rows(self):
        response = self.post(make_request())
        self.assertEqual(response.status_code, 202)
        task = Task.objects.get(pk=response.data["task_id"])
        self.assertEqual(task.task_type, Task.TYPE_RENDER)
        job = RenderJob.objects.get(task=task)
        self.assertEqual(job.payload["clips"][0]["source_url"],
                         "https://example.com/v.mp4")
        self.assertEqual(job.payload["aspect"], "9:16")

    def test_all_400_cases_create_no_rows(self):
        cases = [
            make_request(clips=[]),
            make_request(clips=[make_clip(url="")]),
            make_request(clips=[make_clip(url="not-a-url")]),
            make_request(clips=[make_clip(start=1.0, end=1.0)]),
            make_request(clips=[make_clip(start=2.0, end=1.0)]),
            make_request(clips=[make_clip(), make_clip(rank=1)]),
            make_request(aspect="4:3"),
            make_request(settings={"bgm_id": 99999}),
        ]
        for payload in cases:
            response = self.post(payload)
            self.assertEqual(
                response.status_code, 400, msg=str(response.data)
            )
        self.assertEqual(Task.objects.count(), 0)
        self.assertEqual(RenderJob.objects.count(), 0)

    def test_400_messages_are_clear(self):
        response = self.post(make_request(clips=[make_clip(url="nope")]))
        self.assertIn("source_url", str(response.data))
        response = self.post(make_request(aspect="square"))
        self.assertIn("aspect", str(response.data))


# ---------------------------------------------------------------------------
# Worker: de-dup, reuse, payload rewrite (mocked downloader)
# ---------------------------------------------------------------------------

class UrlsWorkerTests(TestCase):
    def setUp(self):
        super().setUp()
        self.media_root = Path(media_override(self))

    def _make_job(self, clips, master_title="Top Clips", aspect="9:16",
                  settings=None):
        task = Task.objects.create(
            task_type=Task.TYPE_RENDER, status=Task.STATUS_PROCESSING
        )
        payload = {
            "master_title": master_title,
            "aspect": aspect,
            "clips": clips,
            "settings": settings or {},
        }
        job = RenderJob.objects.create(task=task, payload=payload)
        return task, job

    def _mock_downloader(self, files_by_url):
        """Patch YtDlpDownloader; record calls, copy a real file per URL."""
        calls = []

        def download(url, staging_dir, progress_cb=None):
            calls.append(url)
            staging = Path(staging_dir)
            staging.mkdir(parents=True, exist_ok=True)
            target = staging / f"src_{len(calls)}.mp4"
            shutil.copy(files_by_url[url], target)
            if progress_cb:
                progress_cb(5.0)
                progress_cb(90.0)
            return fake_download_result(target)

        patcher = mock.patch(
            "apps.videos.services.downloader.YtDlpDownloader",
        )
        mock_cls = patcher.start()
        mock_cls.return_value.download = download
        self.addCleanup(patcher.stop)
        return calls

    def test_download_called_once_for_shared_url(self):
        # four clips, two unique URLs -> exactly two download calls
        sample = synth_sample()
        calls = self._mock_downloader({
            "https://a.example/one.mp4": sample,
            "https://a.example/two.mp4": sample,
        })
        task, job = self._make_job([
            {"source_url": "https://a.example/one.mp4", "rank": 1,
             "start": 0.0, "end": 1.0, "title": "a"},
            {"source_url": "https://a.example/two.mp4", "rank": 2,
             "start": 0.0, "end": 1.0, "title": "b"},
            {"source_url": "https://a.example/one.mp4", "rank": 3,
             "start": 1.0, "end": 2.0, "title": "c"},
            {"source_url": "https://a.example/one.mp4", "rank": 4,
             "start": 2.0, "end": 3.0, "title": "d"},
        ])

        with mock.patch("apps.renders.services.pipeline.run_render") as stub:
            stub.return_value = {"ok": True}
            urls_svc.run_urls_render(task, job.pk)

        self.assertEqual(len(calls), 2)
        self.assertEqual(
            sorted(calls),
            ["https://a.example/one.mp4", "https://a.example/two.mp4"],
        )
        # every clip was rewritten to a resolved full file
        job.refresh_from_db()
        paths = [c["full_file"] for c in job.payload["clips"]]
        self.assertTrue(all(p and Path(p).is_file() for p in paths))
        # the three clips sharing a URL all point at ONE file
        self.assertEqual(paths[0], paths[2])
        self.assertEqual(paths[0], paths[3])
        self.assertNotIn("source_url", job.payload["clips"][0])
        # run_render received the urls normalize window
        _, kwargs = stub.call_args
        self.assertEqual(
            kwargs.get("normalize_window"),
            (urls_svc.P_NORMALIZE_START, urls_svc.P_NORMALIZE_END),
        )
        # the shared file lives in the persistent full-quality cache
        self.assertEqual(Path(paths[0]).parent.name, "fullcache")
        # staging cleaned
        self.assertFalse(
            (self.media_root / "temp" / ".urls-staging").exists()
        )

    def test_second_job_reuses_cache_without_download(self):
        sample = synth_sample()
        calls = self._mock_downloader({"https://a.example/one.mp4": sample})

        def run_once():
            task, job = self._make_job([
                {"source_url": "https://a.example/one.mp4", "rank": 1,
                 "start": 0.0, "end": 1.0, "title": "a"},
            ])
            with mock.patch(
                "apps.renders.services.pipeline.run_render"
            ) as stub:
                stub.return_value = {"ok": True}
                urls_svc.run_urls_render(task, job.pk)
            job.refresh_from_db()
            return job

        job1 = run_once()
        self.assertEqual(len(calls), 1)
        job2 = run_once()
        self.assertEqual(len(calls), 1)  # cache hit — no second download
        self.assertEqual(
            job1.payload["clips"][0]["full_file"],
            job2.payload["clips"][0]["full_file"],
        )

    def test_video_row_reused_without_download(self):
        video = make_video()
        video.source_url = "https://a.example/fetched.mp4"
        video.save()
        calls = self._mock_downloader({})
        task, job = self._make_job([
            {"source_url": "https://a.example/fetched.mp4", "rank": 1,
             "start": 0.0, "end": 1.0, "title": "a"},
        ])
        with mock.patch("apps.renders.services.pipeline.run_render") as stub:
            stub.return_value = {"ok": True}
            urls_svc.run_urls_render(task, job.pk)
        self.assertEqual(calls, [])  # Video row satisfied the URL
        job.refresh_from_db()
        self.assertEqual(
            Path(job.payload["clips"][0]["full_file"]),
            Path(video.file.path),
        )

    def test_download_failure_fails_with_clear_error(self):
        def boom(url, staging_dir, progress_cb=None):
            raise RuntimeError("404 not found")

        instance = mock.Mock()
        instance.download = boom
        with mock.patch(
            "apps.videos.services.downloader.YtDlpDownloader",
            return_value=instance,
        ):
            task, job = self._make_job([
                {"source_url": "https://a.example/gone.mp4", "rank": 1,
                 "start": 0.0, "end": 1.0, "title": "a"},
            ])
            with self.assertRaises(urls_svc.UrlsRenderError) as caught:
                urls_svc.run_urls_render(task, job.pk)
        self.assertIn("gone.mp4", str(caught.exception))

    def test_progress_reaches_download_window(self):
        seen = []

        def fake_set_progress(task_id, value):
            seen.append(float(value))

        sample = synth_sample()
        calls = self._mock_downloader({"https://a.example/one.mp4": sample})
        task, job = self._make_job([
            {"source_url": "https://a.example/one.mp4", "rank": 1,
             "start": 0.0, "end": 1.0, "title": "a"},
        ])
        with mock.patch(
            "apps.renders.services.pipeline.run_render"
        ) as stub, mock.patch(
            "apps.renders.services.urls.set_progress", fake_set_progress
        ):
            stub.return_value = {"ok": True}
            urls_svc.run_urls_render(task, job.pk)
        # download phase values stay inside 0..30 and complete at 30
        self.assertTrue(seen)
        self.assertLessEqual(max(seen), 30.0)
        self.assertEqual(max(seen), 30.0)
        self.assertEqual(len(calls), 1)


# ---------------------------------------------------------------------------
# Preview endpoint
# ---------------------------------------------------------------------------

class UrlPreviewEndpointTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.media_root = Path(media_override(self))

    def test_missing_url_returns_400(self):
        response = self.client.get(reverse("render-urls-preview"))
        self.assertEqual(response.status_code, 400)

    def test_invalid_url_returns_400(self):
        response = self.client.get(
            reverse("render-urls-preview"), {"url": "not-a-url"}
        )
        self.assertEqual(response.status_code, 400)

    def test_uncached_url_returns_404(self):
        response = self.client.get(
            reverse("render-urls-preview"),
            {"url": "https://example.com/never-seen.mp4"},
        )
        self.assertEqual(response.status_code, 404)

    def test_cached_url_returns_proxy_url(self):
        import hashlib

        url = "https://example.com/cached.mp4"
        digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        cache_dir = self.media_root / "previews" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / f"{digest}.mp4"
        shutil.copy(synth_sample(), cached)

        response = self.client.get(
            reverse("render-urls-preview"), {"url": url}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["proxy_url"], f"/media/previews/cache/{digest}.mp4"
        )
        # the cached file itself must be untouched
        self.assertTrue(cached.is_file())

    def test_empty_cache_entry_returns_404(self):
        import hashlib

        url = "https://example.com/empty.mp4"
        digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        cache_dir = self.media_root / "previews" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{digest}.mp4").write_bytes(b"")
        response = self.client.get(
            reverse("render-urls-preview"), {"url": url}
        )
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# Endpoint → real runner thread → mocked downloader + stubbed pipeline
# ---------------------------------------------------------------------------

class UrlRenderSubmitTests(TransactionTestCase):
    """202 → PENDING/PROCESSING → SUCCESS with real task-runner threads.

    The downloader is mocked (a real local media file per URL) and the
    pipeline is stubbed at run_render, so the flow exercises the view,
    the runner, the worker's URL resolution/de-dup and the result path
    without network or ffmpeg.
    """

    client_class = APIClient

    def test_full_flow_succeeds(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        sample = synth_sample()
        calls = []

        def download(url, staging_dir, progress_cb=None):
            calls.append(url)
            staging = Path(staging_dir)
            staging.mkdir(parents=True, exist_ok=True)
            target = staging / "src.mp4"
            shutil.copy(sample, target)
            if progress_cb:
                progress_cb(90.0)
            return fake_download_result(target)

        def stub_render(task, job_pk, normalize_window=None):
            from apps.core import runner

            runner.set_progress(task.id, 50.0)
            return {"download_url": "/media/renders/abc.mp4",
                    "preview_url": "/media/renders/abc.mp4",
                    "output_file": "abc.mp4",
                    "encoder_used": "libx264"}

        with override_settings(MEDIA_ROOT=media.name), \
                mock.patch(
                    "apps.videos.services.downloader.YtDlpDownloader"
                ) as dl_cls, \
                mock.patch(
                    "apps.renders.services.pipeline.run_render", stub_render
                ):
            dl_cls.return_value.download = download
            payload = make_request(clips=[
                make_clip(url="https://a.example/shared.mp4", rank=1),
                make_clip(url="https://a.example/shared.mp4", rank=2,
                          start=1.0, end=2.0),
            ])
            response = self.client.post(
                reverse("render-urls"), payload, format="json"
            )
            self.assertEqual(response.status_code, 202)
            task_id = response.data["task_id"]

            done = wait_for_task(
                self.client, task_id,
                lambda r: r.data["status"] in (
                    Task.STATUS_SUCCESS, Task.STATUS_FAILED),
            )

        self.assertEqual(
            done.data["status"], Task.STATUS_SUCCESS,
            msg=done.data.get("error"),
        )
        self.assertEqual(done.data["task_type"], Task.TYPE_RENDER)
        self.assertEqual(
            done.data["result"]["download_url"], "/media/renders/abc.mp4"
        )
        # two clips, one unique URL -> one download
        self.assertEqual(calls, ["https://a.example/shared.mp4"])


# ---------------------------------------------------------------------------
# Real end-to-end render (network + real ffmpeg + ffprobe)
# ---------------------------------------------------------------------------

class UrlRenderE2ETests(TransactionTestCase):
    """POST /api/render/urls/ with two clips sharing one real URL.

    Downloads the verified public sample at full quality, renders through
    the real pipeline (NVENC with libx264 fallback) and verifies the
    output with ffprobe: 1080x1920, H.264, audio present, correct trim
    durations, encoder_used recorded on the job.
    """

    def test_two_clips_same_url_render_1080x1920(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        with override_settings(MEDIA_ROOT=media.name):
            payload = make_request(
                clips=[
                    {"source_url": E2E_SOURCE_URL, "rank": 1,
                     "start": 0.0, "end": 3.0, "title": "Bunny Part 1"},
                    {"source_url": E2E_SOURCE_URL, "rank": 2,
                     "start": 5.0, "end": 8.0, "title": "Bunny Part 2"},
                ],
                master_title="Big Buck Countdown",
                aspect="9:16",
            )
            client = APIClient()
            response = client.post(
                reverse("render-urls"), payload, format="json"
            )
            self.assertEqual(
                response.status_code, 202, msg=str(response.data)
            )
            task_id = response.data["task_id"]

            done = wait_for_task(
                client, task_id,
                lambda r: r.data["status"] in (
                    Task.STATUS_SUCCESS, Task.STATUS_FAILED),
                timeout=300.0, interval=1.0,
            )
            self.assertEqual(
                done.data["status"], Task.STATUS_SUCCESS,
                msg=done.data.get("error"),
            )

            result = done.data["result"]
            self.assertIn(result["encoder_used"], ("h264_nvenc", "libx264"))
            self.assertTrue(
                result["download_url"].startswith("/media/renders/")
            )

            task = Task.objects.get(pk=task_id)
            job = RenderJob.objects.get(task=task)
            self.assertEqual(job.encoder_used, result["encoder_used"])
            self.assertEqual(job.output_file, result["output_file"])

            out_path = Path(media.name) / "renders" / result["output_file"]
            self.assertTrue(out_path.is_file())
            self.assertGreater(out_path.stat().st_size, 50_000)

            facts = probe_media(out_path)
            self.assertEqual((facts["width"], facts["height"]), (1080, 1920))
            self.assertEqual(facts["video_codec"], "h264")
            self.assertTrue(facts["has_audio"])
            # 3.0 + 3.0 seconds of clips (trim windows applied)
            self.assertAlmostEqual(facts["duration"], 6.0, delta=0.9)

            # staging cleaned; persistent full cache holds the source once
            self.assertFalse(
                (Path(media.name) / "temp" / ".urls-staging").exists()
            )
            fullcache = Path(media.name) / "temp" / "fullcache"
            cached = list(fullcache.glob("*.mp4")) if fullcache.is_dir() else []
            self.assertEqual(len(cached), 1)
