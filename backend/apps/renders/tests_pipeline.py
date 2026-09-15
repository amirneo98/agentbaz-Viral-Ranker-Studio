"""Tests for the render pipeline, encoder selection and render API.

Layout:

* Pure-builder unit tests (filters, textutil, encoder argv surgery, concat
  list quoting, audio chains) — SimpleTestCase, no DB, no ffmpeg.
* Serializer validation tests — TestCase + temp MEDIA_ROOT.
* API submission tests — the 400 path under TestCase, the 202 path (real
  task runner thread) under TransactionTestCase with a stubbed pipeline.
* Real end-to-end render tests — a genuine network download of a small
  stable sample clip, real ffmpeg normalization/assembly (NVENC with the
  automatic libx264 fallback), real ffprobe verification. These take tens
  of seconds by design; they prove the pipeline actually renders video.
"""
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.core.files.base import ContentFile
from django.test import (
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.urls import reverse

from rest_framework.test import APIClient, APITestCase

from apps.bgm.models import BgmTrack
from apps.core.models import Task
from apps.core.tests import wait_for_task
from apps.renders.models import RenderJob
from apps.renders.serializers import (
    MAX_TITLE_CHARS,
    RenderRequestSerializer,
    build_payload,
)
from apps.renders.services import audio as audio_svc
from apps.renders.services import encoder as encoder_svc
from apps.renders.services import filters as filters_svc
from apps.renders.services import textutil
from apps.renders.services.encoder import EncoderError
from apps.renders.services.pipeline import (
    RenderError,
    _write_concat_list,
    build_final_cmd,
    run_render,
)
from apps.videos.models import Video
from apps.videos.services.probe import probe_media

#: The render tests synthesize their source clips locally with ffmpeg
#: (testsrc2 video + sine audio) instead of depending on an external host:
#: fully deterministic, real media files, and the sample carries an audio
#: stream so the ducking/mix path is exercised too.
SAMPLE_DURATION = 10.0

SAMPLE_CACHE = {}  # process-level sample cache


def synth_sample(with_audio=True):
    """Create (and cache) a real h264/aac test clip with ffmpeg.

    Returns the path of a 640x360, 30 fps, ~10 s mp4.
    """
    key = "audio" if with_audio else "silent"
    if key not in SAMPLE_CACHE:
        fd, path = tempfile.mkstemp(prefix=f"vrs-sample-{key}-", suffix=".mp4")
        os.close(fd)
        os.unlink(path)
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={SAMPLE_DURATION}",
        ]
        if with_audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={SAMPLE_DURATION}"]
        cmd += [
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        ]
        if with_audio:
            cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
        cmd += [path]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        SAMPLE_CACHE[key] = path
    return SAMPLE_CACHE[key]


def make_video(with_audio=True, duration=None):
    """Create a Video row backed by a real synthesized clip in MEDIA_ROOT."""
    src = Path(synth_sample(with_audio=with_audio))
    facts = probe_media(src)
    video = Video(
        source_url="https://example.com/sample.mp4",
        title="Synth Clip" if with_audio else "Silent Clip",
        duration=duration if duration is not None else facts["duration"],
        width=facts["width"],
        height=facts["height"],
        has_audio=facts["has_audio"],
    )
    video.file.save("sample.mp4", ContentFile(src.read_bytes()), save=False)
    video.save()
    return video


def synth_wav(path, frequency=440, duration=2):
    """Create a small sine wav with ffmpeg (real file, real duration)."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"sine=frequency={frequency}:duration={duration}",
            str(path),
        ],
        check=True, capture_output=True, text=True,
    )
    return path


def media_override(testcase):
    """Point MEDIA_ROOT at a per-test temp dir and clean it up afterwards."""
    media = tempfile.TemporaryDirectory()
    testcase.addCleanup(media.cleanup)
    override = override_settings(MEDIA_ROOT=media.name)
    override.enable()
    testcase.addCleanup(override.disable)
    return media.name


def make_bgm_track(media_root, name="Test Loop", frequency=440):
    """Create a BgmTrack backed by a real wav in MEDIA_ROOT."""
    src = Path(tempfile.mkdtemp(prefix="vrs-bgm-")) / "loop.wav"
    synth_wav(src, frequency=frequency)
    track = BgmTrack(name=name, duration=2.0)
    track.file.save("loop.wav", ContentFile(src.read_bytes()), save=False)
    track.save()
    shutil.rmtree(src.parent, ignore_errors=True)
    return track


# ---------------------------------------------------------------------------
# textutil
# ---------------------------------------------------------------------------

class TextUtilTests(SimpleTestCase):
    def test_escape_text_escapes_specials(self):
        escaped = textutil.escape_text("a:b'c\\d%e")
        self.assertNotIn(":", escaped.replace("\\:", ""))
        self.assertNotIn("'", escaped)
        self.assertIn("\u2019", escaped)  # apostrophe → typographic
        self.assertIn("\\%", escaped)
        self.assertIn("\\\\", escaped)

    def test_escape_text_preserves_plain_text(self):
        self.assertEqual(textutil.escape_text("#1"), "#1")

    def test_write_text_file_roundtrip(self):
        tmp = tempfile.mkdtemp(prefix="vrs-text-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = textutil.write_text_file("héllo: wörld", directory=tmp)
        self.addCleanup(os.unlink, path)
        self.assertTrue(str(path).startswith(tmp))
        self.assertTrue(str(path).endswith(".txt"))
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "héllo: wörld")


# ---------------------------------------------------------------------------
# filter builders
# ---------------------------------------------------------------------------

class FilterBuilderTests(SimpleTestCase):
    def test_even_rounds_to_even_minimum_two(self):
        self.assertEqual(filters_svc.even(0), 2)
        self.assertEqual(filters_svc.even(1), 2)
        self.assertEqual(filters_svc.even(1535), 1534)
        self.assertEqual(filters_svc.even(1536), 1536)
        self.assertEqual(filters_svc.even(1537), 1536)

    def test_resolve_font_finds_a_real_font(self):
        font = filters_svc.resolve_font()
        self.assertTrue(os.path.isfile(font))
        self.assertTrue(font.endswith(".ttf"))

    def test_build_video_chain_variants(self):
        bg_blur, fg_h, font = filters_svc.build_video_chain(80, True)
        bg_dark, fg_h2, _ = filters_svc.build_video_chain(60, False)
        self.assertIn("boxblur", bg_blur)
        self.assertIn("eq=brightness", bg_dark)
        self.assertIn("crop=1080:1920", bg_blur)
        self.assertEqual(fg_h % 2, 0)
        self.assertEqual(fg_h2 % 2, 0)
        self.assertGreater(fg_h, fg_h2)  # more pct → taller foreground
        self.assertEqual(fg_h, filters_svc.even(1920 * 80 / 100))

    def test_normalize_clip_cmd_without_title(self):
        info = filters_svc.normalize_clip_cmd(
            src_path="/tmp/v.mp4", start=1.0, end=3.5, rank=2, title="  ",
            video_height_pct=80, background_blur=True,
            out_path="/tmp/out.mp4", staging_dir="/tmp/stage",
        )
        self.assertAlmostEqual(info["duration"], 2.5)
        self.assertIsNone(info["title_textfile"])
        joined = ";".join(info["video_parts"])
        self.assertIn("drawtext", joined)  # rank badge is always drawn
        self.assertNotIn("textfile=", joined)
        self.assertIn("loudnorm", info["audio_chain"])

    def test_normalize_clip_cmd_with_title_uses_textfile(self):
        tmp = tempfile.mkdtemp(prefix="vrs-norm-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        info = filters_svc.normalize_clip_cmd(
            src_path="/tmp/v.mp4", start=0.0, end=2.0, rank=1, title="Best: clip 'ever'",
            video_height_pct=80, background_blur=True,
            out_path="/tmp/out.mp4", staging_dir=tmp,
        )
        self.assertIsNotNone(info["title_textfile"])
        self.assertTrue(os.path.isfile(info["title_textfile"]))
        self.assertIn(
            f"textfile='{info['title_textfile']}'",
            ";".join(info["video_parts"]),
        )
        os.unlink(info["title_textfile"])

    def test_build_normalize_cmd_with_audio(self):
        staging = tempfile.mkdtemp(prefix="vrs-normcmd-")
        self.addCleanup(shutil.rmtree, staging, ignore_errors=True)
        cmd = filters_svc.build_normalize_cmd(
            src_path="/tmp/v.mp4", start=1.0, end=3.0, rank=1, title="t",
            video_height_pct=80, background_blur=True, has_audio=True,
            out_path="/tmp/clip.mp4", staging_dir=staging,
        )
        self.assertEqual(cmd[:4], ["-nostdin", "-hide_banner", "-y", "-ss"])
        self.assertIn("-i", cmd)
        self.assertNotIn("lavfi", cmd)
        joined = " ".join(cmd)
        self.assertIn("[0:a]", joined)
        self.assertIn("libx264", joined)
        self.assertIn("aac", joined)
        self.assertEqual(cmd[-1], "/tmp/clip.mp4")

    def test_build_normalize_cmd_without_audio_injects_silence(self):
        staging = tempfile.mkdtemp(prefix="vrs-normcmd-")
        self.addCleanup(shutil.rmtree, staging, ignore_errors=True)
        cmd = filters_svc.build_normalize_cmd(
            src_path="/tmp/v.mp4", start=0.0, end=2.0, rank=1, title="t",
            video_height_pct=80, background_blur=True, has_audio=False,
            out_path="/tmp/clip.mp4", staging_dir=staging,
        )
        joined = " ".join(cmd)
        self.assertIn("anullsrc", joined)
        self.assertIn("[1:a]", joined)  # silence is input 1


# ---------------------------------------------------------------------------
# encoder selection
# ---------------------------------------------------------------------------

class EncoderUnitTests(SimpleTestCase):
    def test_swap_to_x264_replaces_the_whole_block(self):
        cmd = ["-map", "[vout]", "-map", "[aout]"] + list(encoder_svc.NVENC_ARGS) + [
            "-c:a", "aac",
        ]
        swapped = encoder_svc.swap_to_x264(cmd)
        self.assertEqual(
            swapped,
            ["-map", "[vout]", "-map", "[aout]"] + list(encoder_svc.X264_ARGS)
            + ["-c:a", "aac"],
        )
        self.assertNotIn("h264_nvenc", swapped)
        self.assertIn("libx264", swapped)
        # The original command is untouched (pure function).
        self.assertIn("h264_nvenc", cmd)

    def test_swap_raises_without_codec_block(self):
        with self.assertRaises(EncoderError):
            encoder_svc.swap_to_x264(["-i", "x.mp4"])

    def test_nvenc_failure_signatures_match(self):
        for stderr in (
            "Cannot load libnvidia-encode.so",
            "OpenEncodeSessionEx failed",
            "Driver does not support the required nvenc API version",
            "No NVIDIA capable devices found",
        ):
            self.assertRegex(
                stderr, encoder_svc.NVENC_FAILURE_RE, msg=stderr,
            )

    def test_attempt_returns_nvenc_when_it_works(self):
        with mock.patch.object(
            encoder_svc, "_run_ffmpeg", return_value=(0, "")
        ) as run:
            used = encoder_svc.attempt_video_encode(["-i", "x.mp4", "-c:v", "h264_nvenc"])
        self.assertEqual(used, "h264_nvenc")
        self.assertEqual(run.call_count, 1)

    def test_attempt_falls_back_to_x264_on_nvenc_failure(self):
        results = [(1, "Cannot load libnvidia-encode.so.1"), (0, "")]
        with mock.patch.object(
            encoder_svc, "_run_ffmpeg", side_effect=results
        ) as run:
            used = encoder_svc.attempt_video_encode(
                ["-i", "x.mp4", "-c:v", "h264_nvenc", "-preset", "p4"]
            )
        self.assertEqual(used, "libx264")
        self.assertEqual(run.call_count, 2)
        second_cmd = run.call_args_list[1][0][0]
        self.assertIn("libx264", second_cmd)
        self.assertNotIn("h264_nvenc", second_cmd)
        self.assertNotIn("p4", second_cmd)

    def test_attempt_raises_when_everything_fails(self):
        with mock.patch.object(
            encoder_svc, "_run_ffmpeg", return_value=(1, "some error")
        ):
            with self.assertRaises(EncoderError) as caught:
                encoder_svc.attempt_video_encode(
                    ["-i", "x.mp4", "-c:v", "h264_nvenc"]
                )
        self.assertIn("some error", str(caught.exception))


# ---------------------------------------------------------------------------
# concat list + final command
# ---------------------------------------------------------------------------

class ConcatListTests(SimpleTestCase):
    def _roundtrip(self, line, original):
        """Undo the concat-demuxer quoting and compare with the source."""
        self.assertTrue(line.startswith("file '"))
        self.assertTrue(line.endswith("'"))
        inner = line[len("file '"):-1]
        # concat demuxer: '\'' splices a literal single quote back in.
        self.assertEqual(inner.replace("'\\''", "'"), str(original))

    def test_quotes_paths_with_spaces_and_apostrophes(self):
        staging = Path(tempfile.mkdtemp(prefix="vrs concat ' stage-"))
        self.addCleanup(shutil.rmtree, staging, ignore_errors=True)
        paths = [
            staging / "clip_0.mp4",
            Path("/tmp/dir with space/it's.mp4"),
            Path("/tmp/plain.mp4"),
        ]
        list_path = _write_concat_list(paths, staging)
        self.addCleanup(os.unlink, list_path)
        lines = list_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)
        for line, original in zip(lines, paths):
            self._roundtrip(line, original)

    def test_final_cmd_shape_without_bgm(self):
        staging = Path(tempfile.mkdtemp(prefix="vrs-final-"))
        self.addCleanup(shutil.rmtree, staging, ignore_errors=True)
        cmd = build_final_cmd(
            concat_list=staging / "list.txt", master_title="Top 3",
            bgm_track=None, bgm_volume=0.4, total_duration=6.0,
            out_path=staging / "final.mp4", staging_dir=staging,
        )
        joined = " ".join(cmd)
        self.assertIn("-f concat", joined)
        self.assertIn("-safe 0", joined)
        self.assertNotIn("-stream_loop", joined)
        self.assertIn("textfile=", joined)  # master title drawn
        self.assertIn("[0:a]aformat", joined)  # plain audio chain
        self.assertIn("h264_nvenc", joined)
        self.assertIn("+faststart", joined)
        self.assertEqual(cmd[-1], str(staging / "final.mp4"))

    def test_final_cmd_shape_with_bgm(self):
        staging = Path(tempfile.mkdtemp(prefix="vrs-final-"))
        self.addCleanup(shutil.rmtree, staging, ignore_errors=True)
        bgm_stub = SimpleNamespace(file=SimpleNamespace(path="/tmp/bgm loop.wav"))

        cmd = build_final_cmd(
            concat_list=staging / "list.txt", master_title="",
            bgm_track=bgm_stub, bgm_volume=0.25, total_duration=4.5,
            out_path=staging / "final.mp4", staging_dir=staging,
        )
        joined = " ".join(cmd)
        self.assertIn("-stream_loop -1", joined)
        self.assertIn("-i /tmp/bgm loop.wav", joined)
        self.assertNotIn("textfile=", joined)  # no master title
        self.assertIn("sidechaincompress", joined)
        self.assertIn("volume=0.2500", joined)


# ---------------------------------------------------------------------------
# audio chains
# ---------------------------------------------------------------------------

class AudioChainTests(SimpleTestCase):
    def test_bgm_chain_ducks_and_mixes(self):
        chain = audio_svc.build_bgm_chain(total_duration=6.25, bgm_volume=0.4)
        self.assertIn("atrim=duration=6.250000", chain)
        self.assertIn("volume=0.4000", chain)
        self.assertIn("sidechaincompress", chain)
        self.assertIn("amix=inputs=2", chain)
        self.assertIn("[aout]", chain)
        # The bgm is compressed BY the dialogue ([bgm][0:a]).
        self.assertLess(chain.index("[bgm][0:a]"), chain.index("[ducked]"))

    def test_plain_chain_passes_dialogue(self):
        chain = audio_svc.build_plain_audio_chain()
        self.assertEqual(
            chain,
            "[0:a]aformat=sample_rates=48000:channel_layouts=stereo[aout]",
        )


# ---------------------------------------------------------------------------
# serializer validation
# ---------------------------------------------------------------------------

class RenderSerializerTests(TestCase):
    def setUp(self):
        super().setUp()
        self.media_root = media_override(self)
        self.video = make_video()

    def _data(self, **overrides):
        clip = {
            "video_id": self.video.id, "rank": 1,
            "start": 0.0, "end": 2.0, "title": "A",
        }
        data = {
            "master_title": "Top 1",
            "clips": [clip],
            "settings": {"video_height_pct": 80, "background_blur": True,
                         "bgm_id": None, "bgm_volume": 0.4},
        }
        data.update(overrides)
        return data

    def _assert_invalid(self, data, needle=None):
        serializer = RenderRequestSerializer(data=data)
        self.assertFalse(
            serializer.is_valid(), msg=f"unexpectedly valid: {data!r}"
        )
        if needle is not None:
            self.assertIn(needle, str(serializer.errors))

    def test_minimal_payload_is_valid_with_defaults(self):
        serializer = RenderRequestSerializer(data={
            "clips": [{"video_id": self.video.id, "rank": 1, "start": 0.0, "end": 1.0}],
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_rejects_missing_clips(self):
        self._assert_invalid({"master_title": "x"})

    def test_rejects_empty_clips(self):
        self._assert_invalid(self._data(clips=[]))

    def test_rejects_duplicate_ranks(self):
        second = {"video_id": self.video.id, "rank": 1, "start": 0.0, "end": 1.0}
        self._assert_invalid(self._data(clips=[
            {"video_id": self.video.id, "rank": 1, "start": 0.0, "end": 1.0}, second,
        ]), needle="unique")

    def test_rejects_start_not_before_end(self):
        self._assert_invalid(
            self._data(clips=[{
                "video_id": self.video.id, "rank": 1, "start": 2.0, "end": 2.0,
            }]),
            needle="greater than start",
        )

    def test_rejects_end_beyond_duration(self):
        self._assert_invalid(
            self._data(clips=[{
                "video_id": self.video.id, "rank": 1,
                "start": 0.0, "end": self.video.duration + 5.0,
            }]),
            needle="exceeds duration",
        )

    def test_rejects_unknown_video(self):
        self._assert_invalid(
            self._data(clips=[{
                "video_id": 99999, "rank": 1, "start": 0.0, "end": 1.0,
            }]),
            needle="does not exist",
        )

    def test_rejects_unknown_bgm(self):
        self._assert_invalid(
            self._data(settings={"bgm_id": 99999}), needle="does not exist",
        )

    def test_rejects_out_of_bounds_settings(self):
        self._assert_invalid(
            self._data(settings={"video_height_pct": 10})
        )
        self._assert_invalid(self._data(settings={"bgm_volume": 2.0}))

    def test_valid_bgm_is_accepted(self):
        track = make_bgm_track(self.media_root)
        serializer = RenderRequestSerializer(data=self._data(settings={
            "bgm_id": track.id, "bgm_volume": 0.5,
        }))
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_build_payload_applies_defaults(self):
        serializer = RenderRequestSerializer(data={
            "clips": [{"video_id": self.video.id, "rank": 1, "start": 0.0, "end": 1.0}],
        })
        serializer.is_valid(raise_exception=True)
        payload = build_payload(serializer.validated_data)
        self.assertEqual(payload["master_title"], "")
        self.assertEqual(
            payload["settings"],
            {"video_height_pct": 80, "background_blur": True,
             "bgm_id": None, "bgm_volume": 0.4},
        )

    def test_build_payload_falls_back_and_truncates_titles(self):
        long_title = "x" * (MAX_TITLE_CHARS + 10)
        serializer = RenderRequestSerializer(data={
            "master_title": "Top 2",
            "clips": [
                # no title → falls back to the video's own title
                {"video_id": self.video.id, "rank": 2, "start": 0.0, "end": 1.0},
                # over-long title → truncated
                {"video_id": self.video.id, "rank": 1, "start": 1.0,
                 "end": 2.0, "title": long_title},
            ],
        })
        serializer.is_valid(raise_exception=True)
        payload = build_payload(serializer.validated_data)
        by_rank = {c["rank"]: c for c in payload["clips"]}
        self.assertEqual(by_rank[2]["title"], self.video.title)
        self.assertEqual(len(by_rank[1]["title"]), MAX_TITLE_CHARS)
        self.assertEqual(payload["master_title"], "Top 2")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class RenderValidationApiTests(APITestCase):
    """POST /api/render/ — validation failures (no task may be created)."""

    def setUp(self):
        super().setUp()
        self.media_root = media_override(self)
        self.video = make_video()

    def post(self, payload):
        return self.client.post(reverse("render"), payload, format="json")

    def test_missing_clips_returns_400(self):
        response = self.post({"master_title": "x"})
        self.assertEqual(response.status_code, 400)

    def test_unknown_video_returns_400(self):
        response = self.post({"clips": [
            {"video_id": 99999, "rank": 1, "start": 0.0, "end": 1.0},
        ]})
        self.assertEqual(response.status_code, 400)

    def test_end_beyond_duration_returns_400(self):
        response = self.post({"clips": [
            {"video_id": self.video.id, "rank": 1, "start": 0.0,
             "end": self.video.duration + 10.0},
        ]})
        self.assertEqual(response.status_code, 400)

    def test_unknown_bgm_returns_400(self):
        response = self.post({
            "clips": [{"video_id": self.video.id, "rank": 1,
                       "start": 0.0, "end": 1.0}],
            "settings": {"bgm_id": 99999},
        })
        self.assertEqual(response.status_code, 400)

    def test_master_title_too_long_returns_400(self):
        response = self.post({
            "master_title": "y" * 500,
            "clips": [{"video_id": self.video.id, "rank": 1,
                       "start": 0.0, "end": 1.0}],
        })
        self.assertEqual(response.status_code, 400)

    def test_validation_failures_create_no_tasks(self):
        self.post({})
        self.post({"clips": []})
        self.post({"clips": [
            {"video_id": self.video.id, "rank": 1, "start": 5.0, "end": 1.0},
        ]})
        self.assertEqual(Task.objects.count(), 0)
        self.assertEqual(RenderJob.objects.count(), 0)


class RenderSubmitApiTests(TransactionTestCase):
    """POST /api/render/ happy path with a stubbed pipeline on the runner."""

    client_class = APIClient

    def setUp(self):
        super().setUp()
        self.media_root = media_override(self)
        self.video = make_video()

    def test_submit_creates_task_job_and_schedules_pipeline(self):
        calls = {}

        def stub_run_render(task, job_pk):
            from apps.core import runner

            calls["job_pk"] = job_pk
            runner.set_progress(task.id, 50.0)
            return {
                "download_url": "/media/renders/abc.mp4",
                "encoder_used": "libx264",
            }

        payload = {
            "master_title": "Stub Render",
            "clips": [{"video_id": self.video.id, "rank": 1,
                       "start": 0.0, "end": 2.0, "title": "Clip"}],
            "settings": {"bgm_volume": 0.2},
        }
        with mock.patch("apps.renders.views.run_render", stub_run_render):
            response = self.client.post(reverse("render"), payload, format="json")
            self.assertEqual(response.status_code, 202)
            task_id = response.data["task_id"]
            self.assertEqual(str(Task.objects.get(pk=task_id).id), task_id)

            done = wait_for_task(
                self.client, task_id,
                lambda r: r.data["status"] == Task.STATUS_SUCCESS,
            )

        # Task + RenderJob were wired together and the stub really ran.
        self.assertEqual(done.data["task_type"], Task.TYPE_RENDER)
        self.assertEqual(done.data["progress"], 100.0)
        self.assertEqual(
            done.data["result"],
            {"download_url": "/media/renders/abc.mp4", "encoder_used": "libx264"},
        )
        job = RenderJob.objects.get(task_id=task_id)
        self.assertEqual(job.pk, calls["job_pk"])
        self.assertEqual(job.payload["master_title"], "Stub Render")
        self.assertEqual(job.payload["clips"][0]["video_id"], self.video.id)
        self.assertEqual(job.payload["settings"]["bgm_volume"], 0.2)


# ---------------------------------------------------------------------------
# the real pipeline
# ---------------------------------------------------------------------------

class RunRenderRealTests(TestCase):
    """End-to-end renders with real ffmpeg (network + GPU/CPU encoder)."""

    def setUp(self):
        super().setUp()
        self.media_root = Path(media_override(self))
        self.video = make_video()

    def _make_task(self):
        return Task.objects.create(
            task_type=Task.TYPE_RENDER, status=Task.STATUS_PROCESSING
        )

    def _render(self, clips, master_title="", settings=None):
        task = self._make_task()
        job = RenderJob.objects.create(
            task=task,
            payload={
                "master_title": master_title,
                "clips": clips,
                "settings": settings
                or {"video_height_pct": 80, "background_blur": True,
                    "bgm_id": None, "bgm_volume": 0.4},
            },
        )
        return task, job, run_render(task, job.pk)

    def test_render_two_clips_with_bgm_and_master_title(self):
        track = make_bgm_track(self.media_root)
        clips = [
            {"video_id": self.video.id, "rank": 1, "start": 5.0,
             "end": 7.5, "title": "The 'best': one"},
            {"video_id": self.video.id, "rank": 2, "start": 1.0,
             "end": 3.0, "title": ""},
        ]
        task, job, result = self._render(
            clips, master_title="Top 2 Countdown",
            settings={"video_height_pct": 75, "background_blur": True,
                      "bgm_id": track.id, "bgm_volume": 0.3},
        )

        # --- result payload ---
        filename = result["output_file"]
        self.assertTrue(filename.endswith(".mp4"))
        self.assertEqual(result["download_url"], f"/media/renders/{filename}")
        self.assertEqual(result["preview_url"], result["download_url"])
        self.assertIn(result["encoder_used"], ("h264_nvenc", "libx264"))

        # --- persisted job state ---
        job.refresh_from_db()
        self.assertEqual(job.encoder_used, result["encoder_used"])
        self.assertEqual(job.output_file, filename)

        # --- the output file is real, vertical, and audible ---
        out_path = self.media_root / "renders" / filename
        self.assertTrue(out_path.is_file())
        self.assertGreater(out_path.stat().st_size, 50_000)
        facts = probe_media(out_path)
        self.assertEqual(facts["width"], filters_svc.OUT_W)
        self.assertEqual(facts["height"], filters_svc.OUT_H)
        self.assertTrue(facts["has_audio"])
        # 2.5s + 2.0s of clips; tolerate encoder/container slack.
        self.assertAlmostEqual(facts["duration"], 4.5, delta=0.75)
        self.assertAlmostEqual(result["duration"], facts["duration"], delta=0.1)

        # --- task progress ran to completion, staging was cleaned ---
        task.refresh_from_db()
        self.assertEqual(task.progress, 100.0)
        self.assertFalse((self.media_root / "renders" / ".staging").exists())

    def test_render_single_clip_without_bgm(self):
        task, job, result = self._render([
            {"video_id": self.video.id, "rank": 1, "start": 0.0,
             "end": 2.0, "title": "Sole entry"},
        ])
        self.assertIn(result["encoder_used"], ("h264_nvenc", "libx264"))
        out_path = self.media_root / "renders" / result["output_file"]
        facts = probe_media(out_path)
        self.assertEqual((facts["width"], facts["height"]), (1080, 1920))
        self.assertTrue(facts["has_audio"])
        self.assertAlmostEqual(facts["duration"], 2.0, delta=0.5)
        self.assertFalse((self.media_root / "renders" / ".staging").exists())

    def test_missing_video_raises_render_error(self):
        task = self._make_task()
        job = RenderJob.objects.create(
            task=task,
            payload={"master_title": "", "clips": [
                {"video_id": 99999, "rank": 1, "start": 0.0, "end": 1.0},
            ], "settings": {}},
        )
        with self.assertRaises(RenderError) as caught:
            run_render(task, job.pk)
        self.assertIn("99999", str(caught.exception))
        # Failure must not leave staging debris behind.
        self.assertFalse((self.media_root / "renders" / ".staging").exists())

    def test_missing_bgm_track_raises_render_error(self):
        task = self._make_task()
        job = RenderJob.objects.create(
            task=task,
            payload={"master_title": "", "clips": [
                {"video_id": self.video.id, "rank": 1, "start": 0.0, "end": 1.0},
            ], "settings": {"bgm_id": 99999}},
        )
        with self.assertRaises(RenderError) as caught:
            run_render(task, job.pk)
        self.assertIn("99999", str(caught.exception))

    def test_bgm_track_with_missing_file_raises_render_error(self):
        track = make_bgm_track(self.media_root, name="Ghost Loop")
        os.unlink(track.file.path)
        task = self._make_task()
        job = RenderJob.objects.create(
            task=task,
            payload={"master_title": "", "clips": [
                {"video_id": self.video.id, "rank": 1, "start": 0.0, "end": 1.0},
            ], "settings": {"bgm_id": track.id}},
        )
        with self.assertRaises(RenderError) as caught:
            run_render(task, job.pk)
        self.assertIn("missing", str(caught.exception))

    def test_render_job_for_missing_job_raises(self):
        task = self._make_task()
        with self.assertRaises(RenderError):
            run_render(task, uuid.uuid4())
