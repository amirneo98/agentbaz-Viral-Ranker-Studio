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
        # v1.2: gblur with configurable sigma (default 25) replaces boxblur.
        self.assertIn("gblur=sigma=25.00", bg_blur)
        self.assertIn("crop=1080:1920", bg_blur)
        # v1.2: unblurred renders paint onto a solid black canvas instead
        # of a darkened cover (bg chain empty — caller builds color source).
        self.assertEqual(bg_dark, "")
        self.assertEqual(fg_h % 2, 0)
        self.assertEqual(fg_h2 % 2, 0)
        self.assertGreater(fg_h, fg_h2)  # more pct → taller foreground
        self.assertEqual(fg_h, filters_svc.even(1920 * 80 / 100))

        bg_sigma, _, _ = filters_svc.build_video_chain(
            80, True, aspect="16:9", blur_sigma=7.5
        )
        self.assertIn("gblur=sigma=7.50", bg_sigma)
        self.assertIn("crop=1920:1080", bg_sigma)

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

# ---------------------------------------------------------------------------
# v1.2 — textutil style/colour/RTL helpers
# ---------------------------------------------------------------------------

class TextStyleUtilTests(SimpleTestCase):
    def test_has_arabic_detects_persian(self):
        self.assertTrue(textutil.has_arabic("سلام"))
        self.assertTrue(textutil.has_arabic("mixed سلام text"))
        self.assertFalse(textutil.has_arabic("plain english 123"))
        self.assertFalse(textutil.has_arabic(""))

    def test_rtl_fix_passthrough_when_fribidi(self):
        # This host's ffmpeg has fribidi (build conf verified in CI/dev);
        # rtl_fix must be a no-op so drawtext does the real shaping.
        if textutil.ffmpeg_has_fribidi():
            self.assertEqual(textutil.rtl_fix("سلام دنیا"), "سلام دنیا")
        else:
            self.assertNotEqual(textutil.rtl_fix("سلام دنیا"), "سلام دنیا")

    def test_ffmpeg_has_fribidi_matches_build(self):
        # ffmpeg -h filter=drawtext exposes text_shaping only with fribidi.
        probe = subprocess.run(
            ["ffmpeg", "-hide_banner", "-h", "filter=drawtext"],
            capture_output=True, text=True,
        )
        blob = (probe.stdout or "") + (probe.stderr or "")
        self.assertEqual(textutil.ffmpeg_has_fribidi(), "text_shaping" in blob)

    def test_resolve_style_font_known_names(self):
        for name in ("ArchivoBlack", "BebasNeue", "Rubik", "Montserrat",
                     "Vazirmatn", "Archivo Black", "vazirmatn bold"):
            path = textutil.resolve_style_font(name)
            self.assertIsNotNone(path, msg=name)
            self.assertTrue(os.path.isfile(path), msg=name)

    def test_resolve_style_font_unknown_returns_none(self):
        self.assertIsNone(textutil.resolve_style_font("NoSuchFont"))
        self.assertIsNone(textutil.resolve_style_font(""))
        self.assertIsNone(textutil.resolve_style_font(None))

    def test_coerce_style_defaults_match_v11(self):
        style = textutil.coerce_style(None)
        self.assertIsNone(style["font"])          # → system LiberationSans
        self.assertEqual(style["fontSize"], 62)
        self.assertEqual(style["textColor"], "white")
        self.assertEqual(style["strokeWidth"], 0)
        self.assertIsNone(style["shadow"])
        self.assertEqual(style["badgeBg"], "black")
        self.assertAlmostEqual(style["badgeOpacity"], 0.55)
        self.assertEqual(style["textPosition"], "bottom")

    def test_coerce_style_clamps_bad_values(self):
        style = textutil.coerce_style({
            "fontSize": "bogus", "strokeWidth": 999, "badgeOpacity": 5.0,
            "textPosition": "diagonal", "margin": "x",
        })
        self.assertEqual(style["fontSize"], 62)   # fallback
        self.assertEqual(style["strokeWidth"], 20)  # clamped
        self.assertEqual(style["badgeOpacity"], 1.0)
        self.assertEqual(style["textPosition"], "bottom")
        self.assertIsNone(style["margin"])

    def test_normalize_color(self):
        self.assertEqual(textutil.normalize_color("#FFD700"), "0xFFD700")
        self.assertEqual(textutil.normalize_color("red"), "red")
        self.assertEqual(textutil.normalize_color(""), "white")
        self.assertEqual(textutil.normalize_color(None), "white")


# ---------------------------------------------------------------------------
# v1.2 — filter builders: aspects, blur, styled drawtext
# ---------------------------------------------------------------------------

class V12FilterBuilderTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.staging = tempfile.mkdtemp(prefix="vrs-v12fb-")
        self.addCleanup(shutil.rmtree, self.staging, ignore_errors=True)

    def _info(self, **kw):
        base = dict(
            src_path="/tmp/v.mp4", start=1.0, end=3.0, rank=1, title="T",
            video_height_pct=80, background_blur=True,
            out_path="/tmp/o.mp4", staging_dir=self.staging,
        )
        base.update(kw)
        return filters_svc.normalize_clip_cmd(**base)

    def test_aspect_dimensions(self):
        self.assertEqual(filters_svc.out_dims("9:16"), (1080, 1920))
        self.assertEqual(filters_svc.out_dims("16:9"), (1920, 1080))
        self.assertEqual(filters_svc.out_dims("garbage"), (1080, 1920))
        self.assertEqual(filters_svc.out_dims(None), (1080, 1920))

    def test_169_chain_uses_1920x1080(self):
        info = self._info(aspect="16:9")
        joined = ";".join(info["video_parts"])
        self.assertIn("scale=1920:1080:force_original_aspect_ratio", joined)
        self.assertIn("crop=1920:1080", joined)
        self.assertNotIn("1080:1920", joined)

    def test_blur_sigma_configurable(self):
        self.assertIn("gblur=sigma=7.50", self._info(blur_sigma=7.5)["bg"])
        self.assertIn("gblur=sigma=25.00", self._info()["bg"])  # default
        self.assertIn("gblur=sigma=25.00", self._info(blur_sigma="junk")["bg"])

    def test_no_blur_builds_black_canvas(self):
        info = self._info(background_blur=False)
        joined = ";".join(info["video_parts"])
        self.assertEqual(info["bg"], "")
        self.assertIn("color=c=black:s=1080x1920", joined)
        self.assertIn("shortest=1", joined)
        self.assertNotIn("gblur", joined)

    def test_styled_title_drawtext_full_options(self):
        style = {
            "font": "BebasNeue", "fontSize": 72, "textColor": "#FFD700",
            "strokeColor": "black", "strokeWidth": 4, "shadow": True,
            "badgeBg": "red", "badgeOpacity": 0.7, "textPosition": "center",
        }
        info = self._info(style=style, title="Hello")
        frag = info["video_parts"][-1]
        self.assertIn("BebasNeue.ttf", frag)
        self.assertIn("fontsize=72", frag)
        self.assertIn("fontcolor=0xFFD700", frag)
        self.assertIn("borderw=4", frag)
        self.assertIn("bordercolor=black", frag)
        self.assertIn("shadowcolor=black:shadowx=3:shadowy=3", frag)
        self.assertIn("boxcolor=red@0.70", frag)
        self.assertIn("y=(h-text_h)/2", frag)

    def test_position_presets(self):
        self.assertIn("y=h*0.04",
                      self._info(style={"textPosition": "top"})["video_parts"][-1])
        self.assertIn("y=h-text_h-h*0.10",
                      self._info(style={"textPosition": "bottom"})["video_parts"][-1])
        frag = self._info(style={"textPosition": "top", "margin": 200})
        self.assertIn("y=200", frag["video_parts"][-1])

    def test_default_style_matches_v11_look(self):
        info = self._info(style=None, title="Hello")
        frag = info["video_parts"][-1]
        self.assertIn("LiberationSans-Bold.ttf", frag)
        self.assertIn("fontsize=62", frag)
        self.assertIn("fontcolor=white", frag)
        self.assertIn("boxcolor=black@0.55", frag)
        # v1.1 title used boxborderw=22; v1.2 default keeps 12 (spec badge).
        self.assertIn("y=h-text_h-h*0.10", frag)

    def test_subtitle_drawtext_present(self):
        info = self._info(subtitle="sub text", style={"fontSize": 80})
        frag = info["video_parts"][-1]
        self.assertEqual(frag.count("drawtext"), 3)  # rank + title + subtitle
        self.assertIn("fontsize=49", frag)  # 80 * 0.62

    def test_per_clip_volume_in_audio_chain(self):
        self.assertIn("volume=0.5000", self._info(volume=0.5)["audio_chain"])
        self.assertIn("volume=1.5000", self._info(volume=1.5)["audio_chain"])
        self.assertNotIn("volume=", self._info(volume=None)["audio_chain"])
        self.assertNotIn("volume=", self._info(volume=1.0)["audio_chain"])

    def test_volume_applied_after_loudnorm(self):
        chain = self._info(volume=0.5)["audio_chain"]
        self.assertLess(chain.index("loudnorm"), chain.index("volume="))

    def test_hd_input_skips_seek_flags(self):
        cmd = filters_svc.build_normalize_cmd(
            src_path="/tmp/hd.mp4", start=None, end=None, rank=1, title="T",
            video_height_pct=80, background_blur=True, has_audio=True,
            out_path="/tmp/o.mp4", staging_dir=self.staging,
            duration_hint=3.0,
        )
        self.assertNotIn("-ss", cmd)
        self.assertIn("-t", cmd)
        self.assertIn("3.250000", " ".join(cmd))  # duration + guard

    def test_rank_badge_font_follows_style(self):
        info = self._info(style={"font": "Rubik"})
        self.assertIn("Rubik-Bold.ttf", info["video_parts"][-1])


# ---------------------------------------------------------------------------
# v1.2 — encoder: 16:9 NVENC block swap
# ---------------------------------------------------------------------------

class V12EncoderTests(SimpleTestCase):
    def test_nvenc_169_block_swaps_to_x264(self):
        cmd = ["-map", "[vout]"] + list(encoder_svc.NVENC_ARGS_169) + [
            "-c:a", "aac",
        ]
        swapped = encoder_svc.swap_to_x264(cmd)
        self.assertIn("libx264", swapped)
        self.assertNotIn("h264_nvenc", swapped)
        self.assertNotIn("8M", swapped)
        self.assertEqual(swapped[-1], "aac")

    def test_169_block_has_bitrate_hint(self):
        joined = " ".join(encoder_svc.NVENC_ARGS_169)
        self.assertIn("-b:v 8M", joined)
        self.assertIn("h264_nvenc", joined)


# ---------------------------------------------------------------------------
# v1.2 — audio: configurable ducking
# ---------------------------------------------------------------------------

class V12AudioTests(SimpleTestCase):
    def test_default_ducking_matches_v11(self):
        chain = audio_svc.build_bgm_chain(6.0, 0.4)
        self.assertIn("threshold=0.0300", chain)
        self.assertIn("ratio=4.00", chain)

    def test_custom_ducking_params(self):
        chain = audio_svc.build_bgm_chain(
            6.0, 0.4, ducking_threshold=0.05, ducking_ratio=8
        )
        self.assertIn("threshold=0.0500", chain)
        self.assertIn("ratio=8.00", chain)

    def test_ducking_params_clamped(self):
        chain = audio_svc.build_bgm_chain(
            6.0, 0.4, ducking_threshold="junk", ducking_ratio=999
        )
        self.assertIn("threshold=0.0300", chain)
        self.assertIn("ratio=20.00", chain)


# ---------------------------------------------------------------------------
# v1.2 — master title styling
# ---------------------------------------------------------------------------

class V12MasterTitleTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.staging = tempfile.mkdtemp(prefix="vrs-v12mt-")
        self.addCleanup(shutil.rmtree, self.staging, ignore_errors=True)

    def test_master_style_defaults_v11_look(self):
        frag, _ = filters_svc.build_master_title_drawtext(
            "Top 5", self.staging
        )
        self.assertIn("fontsize=84", frag)
        self.assertIn("boxcolor=black@0.55", frag)
        self.assertIn("y=110", frag)

    def test_master_style_custom(self):
        frag, _ = filters_svc.build_master_title_drawtext(
            "Top 5", self.staging, aspect="16:9",
            style={"font": "ArchivoBlack", "fontSize": 100,
                   "textColor": "yellow", "strokeWidth": 2, "shadow": True},
        )
        self.assertIn("ArchivoBlack.ttf", frag)
        self.assertIn("fontsize=100", frag)
        self.assertIn("fontcolor=yellow", frag)
        self.assertIn("borderw=2", frag)
        self.assertIn("shadowx=3", frag)
        self.assertIn("y=60", frag)  # 16:9 top margin

    def test_empty_master_title(self):
        frag, tf = filters_svc.build_master_title_drawtext("", self.staging)
        self.assertEqual((frag, tf), ("", None))

    def test_final_cmd_uses_169_encoder_block(self):
        cmd = build_final_cmd(
            concat_list=Path(self.staging) / "list.txt",
            master_title="", bgm_track=None, bgm_volume=0.4,
            total_duration=4.0, out_path=Path(self.staging) / "f.mp4",
            staging_dir=self.staging, aspect="16:9",
        )
        joined = " ".join(cmd)
        self.assertIn("-b:v 8M", joined)
        cmd916 = build_final_cmd(
            concat_list=Path(self.staging) / "list.txt",
            master_title="", bgm_track=None, bgm_volume=0.4,
            total_duration=4.0, out_path=Path(self.staging) / "f.mp4",
            staging_dir=self.staging, aspect="9:16",
        )
        self.assertIn("-b:v 0", " ".join(cmd916))

    def test_final_cmd_passes_ducking_params(self):
        bgm_stub = SimpleNamespace(file=SimpleNamespace(path="/tmp/bgm.wav"))
        cmd = build_final_cmd(
            concat_list=Path(self.staging) / "list.txt",
            master_title="", bgm_track=bgm_stub, bgm_volume=0.4,
            total_duration=4.0, out_path=Path(self.staging) / "f.mp4",
            staging_dir=self.staging,
            ducking_threshold=0.07, ducking_ratio=6,
        )
        joined = " ".join(cmd)
        self.assertIn("threshold=0.0700", joined)
        self.assertIn("ratio=6.00", joined)


# ---------------------------------------------------------------------------
# v1.2 — serializer
# ---------------------------------------------------------------------------

class V12SerializerTests(TestCase):
    def setUp(self):
        super().setUp()
        self.media_root = media_override(self)
        self.video = make_video()

    def test_v11_payload_shape_unchanged(self):
        serializer = RenderRequestSerializer(data={
            "clips": [{"video_id": self.video.id, "rank": 1,
                       "start": 0.0, "end": 1.0}],
        })
        serializer.is_valid(raise_exception=True)
        payload = build_payload(serializer.validated_data)
        self.assertEqual(
            payload["settings"],
            {"video_height_pct": 80, "background_blur": True,
             "bgm_id": None, "bgm_volume": 0.4},
        )
        self.assertNotIn("aspect", payload)
        self.assertNotIn("master_title_style", payload)

    def test_v12_full_payload_is_valid(self):
        serializer = RenderRequestSerializer(data={
            "aspect": "16:9",
            "master_title": "Top 3",
            "master_title_style": {"font": "Rubik", "fontSize": 90},
            "clips": [{
                "video_id": self.video.id, "rank": 1,
                "start": 0.0, "end": 1.0, "title": "A", "subtitle": "sub",
                "style": {"font": "BebasNeue", "fontSize": 70},
                "volume": 0.5,
            }],
            "settings": {"video_scale_pct": 90, "blur_sigma": 10,
                         "ducking_threshold": 0.05, "ducking_ratio": 8,
                         "aspect": "16:9"},
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        payload = build_payload(serializer.validated_data)
        self.assertEqual(payload["aspect"], "16:9")
        self.assertEqual(payload["master_title_style"]["font"], "Rubik")
        clip = payload["clips"][0]
        self.assertEqual(clip["subtitle"], "sub")
        self.assertEqual(clip["volume"], 0.5)
        self.assertEqual(clip["style"]["font"], "BebasNeue")
        self.assertEqual(payload["settings"]["blur_sigma"], 10.0)
        self.assertEqual(payload["settings"]["ducking_ratio"], 8.0)

    def test_hd_file_clip_requires_no_video_or_times(self):
        serializer = RenderRequestSerializer(data={
            "clips": [{"rank": 1, "hd_file": "/tmp/section.mp4",
                       "title": "HD"}],
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        payload = build_payload(serializer.validated_data)
        clip = payload["clips"][0]
        self.assertNotIn("video_id", clip)
        self.assertNotIn("start", clip)
        self.assertEqual(clip["hd_file"], "/tmp/section.mp4")

    def test_clip_with_neither_video_nor_hd_rejected(self):
        serializer = RenderRequestSerializer(data={
            "clips": [{"rank": 1, "start": 0.0, "end": 1.0}],
        })
        self.assertFalse(serializer.is_valid())

    def test_hd_clip_volume_bounds(self):
        serializer = RenderRequestSerializer(data={
            "clips": [{"rank": 1, "hd_file": "/tmp/x.mp4", "volume": 9.9}],
        })
        self.assertFalse(serializer.is_valid())

    def test_video_clip_still_needs_valid_window(self):
        serializer = RenderRequestSerializer(data={
            "clips": [{"video_id": self.video.id, "rank": 1,
                       "start": 2.0, "end": 1.0}],
        })
        self.assertFalse(serializer.is_valid())


# ---------------------------------------------------------------------------
# v1.2 — pipeline payload coercion
# ---------------------------------------------------------------------------

class V12PayloadCoercionTests(SimpleTestCase):
    def test_v11_payload_coerces_with_defaults(self):
        from apps.renders.services.pipeline import coerce_v12_payload
        (aspect, clips, settings_dict,
         master_title, master_style) = coerce_v12_payload({
            "master_title": "T",
            "clips": [{"video_id": 1, "rank": 2, "start": 0.0,
                       "end": 2.0, "title": "x"}],
            "settings": {"video_height_pct": 75, "background_blur": True,
                         "bgm_id": None, "bgm_volume": 0.4},
        })
        self.assertEqual(aspect, "9:16")
        self.assertEqual(settings_dict["video_height_pct"], 75)
        self.assertEqual(settings_dict["blur_sigma"], 25.0)
        self.assertEqual(settings_dict["ducking_threshold"], 0.03)
        self.assertEqual(settings_dict["ducking_ratio"], 4)
        self.assertIsNone(master_style)
        self.assertIsNone(clips[0]["hd_path"])

    def test_v12_settings_coerced(self):
        from apps.renders.services.pipeline import coerce_v12_payload
        hd = tempfile.mkdtemp(prefix="vrs-coerce-")
        self.addCleanup(shutil.rmtree, hd, ignore_errors=True)
        hd_file = Path(hd) / "x.mp4"
        hd_file.write_bytes(b"placeholder")  # existence check only
        _, clips, s, _, _ = coerce_v12_payload({
            "aspect": "16:9",
            "clips": [{"rank": 1, "hd_file": str(hd_file),
                       "video_id": None}],
            "settings": {"video_scale_pct": 90, "blur_sigma": 8,
                         "ducking_threshold": 0.05, "ducking_ratio": 8},
        })
        self.assertEqual(s["video_scale_pct"], 90)
        self.assertEqual(s["blur_sigma"], 8.0)
        self.assertEqual(s["aspect"], "16:9")
        self.assertEqual(clips[0]["hd_path"], hd_file)

    def test_missing_hd_file_raises(self):
        from apps.renders.services.pipeline import coerce_v12_payload
        with self.assertRaises(Exception):
            coerce_v12_payload({
                "clips": [{"rank": 1, "hd_file": "/nonexistent/x.mp4",
                           "video_id": None}],
            })

    def test_all_hd_detection(self):
        from apps.renders.services.pipeline import coerce_v12_payload
        # no hd_file → not all-HD
        _, clips, _, _, _ = coerce_v12_payload({
            "clips": [{"video_id": 1, "rank": 1, "start": 0, "end": 1}],
        })
        self.assertFalse(all(c["hd_path"] is not None for c in clips))


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


# ---------------------------------------------------------------------------
# v1.2 — real end-to-end renders (dual aspect, styles, HD input, Persian)
# ---------------------------------------------------------------------------

def synth_hd_section(directory, name="hd_section.mp4", duration=3.0,
                     with_audio=True):
    """Create a pre-trimmed HD-section style mp4 fixture."""
    path = Path(directory) / name
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"testsrc2=size=1280x720:rate=30:duration={duration}",
    ]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=600:duration={duration}"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
    if with_audio:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
    subprocess.run(cmd + [str(path)], check=True, capture_output=True,
                   text=True)
    return path


def frame_pixels(video_path, at=1.0):
    """Extract one frame and return it as raw RGB bytes."""
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", str(at), "-i", str(video_path), "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ],
        check=True, capture_output=True,
    )
    return proc.stdout


def mean_rgb(raw, width, height):
    """Mean colour of a raw rgb24 frame."""
    n = len(raw) // 3
    return (
        sum(raw[0::3]) / n,
        sum(raw[1::3]) / n,
        sum(raw[2::3]) / n,
    )


class RunRenderV12RealTests(TestCase):
    """Real v1.2 renders: aspects, styles, HD input, volume, Persian."""

    def setUp(self):
        super().setUp()
        self.media_root = Path(media_override(self))
        self.video = make_video()
        self.hd_dir = tempfile.mkdtemp(prefix="vrs-hd-")
        self.addCleanup(shutil.rmtree, self.hd_dir, ignore_errors=True)

    def _make_task(self):
        return Task.objects.create(
            task_type=Task.TYPE_RENDER, status=Task.STATUS_PROCESSING
        )

    def _render(self, payload):
        task = self._make_task()
        job = RenderJob.objects.create(task=task, payload=payload)
        return task, job, run_render(task, job.pk)

    def test_render_169_aspect(self):
        """16:9 renders produce exactly 1920x1080 output."""
        task, job, result = self._render({
            "aspect": "16:9",
            "master_title": "Wide Test",
            "clips": [
                {"video_id": self.video.id, "rank": 1, "start": 0.0,
                 "end": 2.0, "title": "One"},
                {"video_id": self.video.id, "rank": 2, "start": 2.0,
                 "end": 4.0, "title": "Two"},
            ],
            "settings": {"video_height_pct": 80, "background_blur": True},
        })
        out_path = self.media_root / "renders" / result["output_file"]
        facts = probe_media(out_path)
        self.assertEqual((facts["width"], facts["height"]), (1920, 1080))
        self.assertTrue(facts["has_audio"])
        self.assertAlmostEqual(facts["duration"], 4.0, delta=0.75)
        self.assertEqual(result["aspect"], "16:9")
        self.assertIn(result["encoder_used"], ("h264_nvenc", "libx264"))
        self.assertFalse((self.media_root / "renders" / ".staging").exists())

    def test_render_169_no_blur_solid_black(self):
        """background_blur=False paints a solid black background."""
        _, _, result = self._render({
            "aspect": "16:9",
            "clips": [
                {"video_id": self.video.id, "rank": 1, "start": 0.0,
                 "end": 2.0, "title": "One"},
            ],
            "settings": {"video_height_pct": 60, "background_blur": False},
        })
        out_path = self.media_root / "renders" / result["output_file"]
        facts = probe_media(out_path)
        self.assertEqual((facts["width"], facts["height"]), (1920, 1080))
        # Sample a corner far from the centered 60%-height fg: it must be
        # pure black, proving no blurred cover was painted.
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", "1", "-i", str(out_path), "-frames:v", "1",
             "-vf", "crop=40:40:10:10", "-f", "rawvideo",
             "-pix_fmt", "rgb24", "pipe:1"],
            check=True, capture_output=True,
        )
        px = proc.stdout
        mean = mean_rgb(px, 40, 40)
        self.assertLess(sum(mean) / 3, 12.0, msg=f"corner={mean}")

    def test_render_custom_blur_sigma_changes_pixels(self):
        """sigma=2 vs sigma=60 produce visibly different backgrounds."""
        results = []
        for sigma in (2, 60):
            _, _, result = self._render({
                "clips": [{
                    "video_id": self.video.id, "rank": 1, "start": 0.0,
                    "end": 2.0, "title": "B",
                }],
                "settings": {"video_height_pct": 60,
                             "background_blur": True, "blur_sigma": sigma},
            })
            out = self.media_root / "renders" / result["output_file"]
            results.append(frame_pixels(out, at=1.0))
        self.assertNotEqual(results[0], results[1])

    def test_per_clip_styles_apply_different_fonts_and_colors(self):
        """Two clips with different styles render visibly different frames."""
        _, _, result = self._render({
            "master_title": "",
            "clips": [
                {"video_id": self.video.id, "rank": 2, "start": 0.0,
                 "end": 2.0, "title": "YELLOW BADGE",
                 "style": {"font": "ArchivoBlack", "fontSize": 64,
                           "textColor": "#FFFF00", "badgeBg": "red",
                           "badgeOpacity": 0.9, "textPosition": "center"}},
                {"video_id": self.video.id, "rank": 1, "start": 0.0,
                 "end": 2.0, "title": "CYAN TOP",
                 "style": {"font": "BebasNeue", "fontSize": 72,
                           "textColor": "#00FFFF", "badgeBg": "black",
                           "badgeOpacity": 0.9, "textPosition": "top",
                           "margin": 300}},
            ],
            "settings": {"video_height_pct": 80, "background_blur": True},
        })
        out_path = self.media_root / "renders" / result["output_file"]
        # rank 2 renders first (countdown), rank 1 second
        frame_a = frame_pixels(out_path, at=0.8)   # clip 1: rank2 yellow
        frame_b = frame_pixels(out_path, at=2.8)   # clip 2: rank1 cyan

        def has_color(raw, rgb, tol=36):
            step = 3 * 8  # subsample for speed
            for i in range(0, len(raw) - 2, step):
                if (abs(raw[i] - rgb[0]) < tol
                        and abs(raw[i + 1] - rgb[1]) < tol
                        and abs(raw[i + 2] - rgb[2]) < tol):
                    return True
            return False

        self.assertTrue(has_color(frame_a, (255, 255, 0)),
                        msg="yellow title text not found in clip 1 frame")
        self.assertTrue(has_color(frame_b, (0, 255, 255)),
                        msg="cyan title text not found in clip 2 frame")

    def test_stroke_shadow_badge_really_render(self):
        """stroke/shadow/badge options produce pixels a plain render lacks."""
        plain = self._render({
            "clips": [{
                "video_id": self.video.id, "rank": 1, "start": 0.0,
                "end": 2.0, "title": "Styled",
                "style": {"textPosition": "center"},
            }],
            "settings": {"video_height_pct": 80, "background_blur": True},
        })[2]
        styled = self._render({
            "clips": [{
                "video_id": self.video.id, "rank": 1, "start": 0.0,
                "end": 2.0, "title": "Styled",
                "style": {"fontSize": 80, "textColor": "#FFFFFF",
                          "strokeColor": "#FF00FF", "strokeWidth": 6,
                          "shadow": True, "badgeBg": "green",
                          "badgeOpacity": 0.85, "textPosition": "center"},
            }],
            "settings": {"video_height_pct": 80, "background_blur": True},
        })[2]
        plain_px = frame_pixels(
            self.media_root / "renders" / plain["output_file"], at=1.0
        )
        styled_px = frame_pixels(
            self.media_root / "renders" / styled["output_file"], at=1.0
        )
        self.assertNotEqual(plain_px, styled_px)
        # magenta stroke pixels must exist
        found = any(
            styled_px[i] > 150 and styled_px[i + 1] < 110
            and styled_px[i + 2] > 150
            for i in range(0, len(styled_px) - 2, 3 * 4)
        )
        self.assertTrue(found, msg="no magenta stroke pixels found")
        # green badge pixels must exist
        found = any(
            styled_px[i] < 110 and styled_px[i + 1] > 150
            and styled_px[i + 2] < 110
            for i in range(0, len(styled_px) - 2, 3 * 4)
        )
        self.assertTrue(found, msg="no green badge pixels found")

    def test_hd_file_used_directly_without_trim(self):
        """A pre-downloaded section is rendered as-is (no -ss/-to)."""
        hd = synth_hd_section(self.hd_dir, duration=3.0)
        task, job, result = self._render({
            "clips": [{
                "rank": 1, "hd_file": str(hd),
                "title": "HD Direct", "subtitle": "from section",
                "style": {"font": "Rubik", "fontSize": 66},
                "volume": 0.8,
            }],
            "settings": {"video_height_pct": 80, "background_blur": True},
        })
        out_path = self.media_root / "renders" / result["output_file"]
        facts = probe_media(out_path)
        # natural 3 s duration preserved (no trim), 9:16 normalization
        self.assertEqual((facts["width"], facts["height"]), (1080, 1920))
        self.assertAlmostEqual(facts["duration"], 3.0, delta=0.6)
        self.assertTrue(facts["has_audio"])
        task.refresh_from_db()
        self.assertEqual(task.progress, 100.0)

    def test_hd_file_media_relative_path(self):
        """hd_file may be relative to MEDIA_ROOT."""
        # copy fixture into media root
        rel_dir = self.media_root / "sections"
        rel_dir.mkdir(exist_ok=True)
        hd = synth_hd_section(rel_dir, duration=2.0)
        _, _, result = self._render({
            "clips": [{
                "rank": 1,
                "hd_file": f"sections/{hd.name}",
                "title": "Relative",
            }],
            "settings": {"video_height_pct": 80, "background_blur": True},
        })
        facts = probe_media(
            self.media_root / "renders" / result["output_file"]
        )
        self.assertEqual((facts["width"], facts["height"]), (1080, 1920))
        self.assertAlmostEqual(facts["duration"], 2.0, delta=0.6)

    def test_per_clip_volume_changes_output_level(self):
        """A quieter clip produces a measurably quieter track."""
        def render_with(volume):
            _, _, result = self._render({
                "clips": [{
                    "video_id": self.video.id, "rank": 1, "start": 0.0,
                    "end": 2.0, "title": "V", "volume": volume,
                }],
                "settings": {"video_height_pct": 80, "background_blur": True,
                             "bgm_id": None},
            })
            return self.media_root / "renders" / result["output_file"]

        def mean_level(path):
            proc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-i", str(path),
                 "-af", "volumedetect", "-f", "null", "-"],
                capture_output=True, text=True,
            )
            import re as _re
            m = _re.search(r"mean_volume: ([-\d.]+) dB",
                           proc.stderr or "")
            return float(m.group(1)) if m else None

        loud = mean_level(render_with(1.0))
        quiet = mean_level(render_with(0.15))
        self.assertIsNotNone(loud)
        self.assertIsNotNone(quiet)
        self.assertLess(quiet, loud - 6.0,
                        msg=f"quiet={quiet} loud={loud}")

    def test_persian_text_render_no_error(self):
        """Persian titles (RTL + shaping via fribidi) render without error."""
        _, _, result = self._render({
            "master_title": "۵ کلیپ برتر",
            "master_title_style": {"font": "Vazirmatn", "fontSize": 90,
                                   "textColor": "#FFD700"},
            "clips": [{
                "video_id": self.video.id, "rank": 1, "start": 0.0,
                "end": 2.0, "title": "عنوان فارسی برای کلیپ شماره یک",
                "subtitle": "زیرنویس آزمایشی",
                "style": {"font": "Vazirmatn", "fontSize": 68,
                          "textColor": "white", "strokeWidth": 3,
                          "shadow": True, "badgeBg": "navy",
                          "badgeOpacity": 0.8},
            }],
            "settings": {"video_height_pct": 80, "background_blur": True},
        })
        out_path = self.media_root / "renders" / result["output_file"]
        facts = probe_media(out_path)
        self.assertEqual((facts["width"], facts["height"]), (1080, 1920))
        self.assertAlmostEqual(facts["duration"], 2.0, delta=0.5)
        # The frame differs from a Latin-title render of the same clip.
        latin = self._render({
            "clips": [{
                "video_id": self.video.id, "rank": 1, "start": 0.0,
                "end": 2.0, "title": "Latin Title One",
                "style": {"font": "Vazirmatn", "fontSize": 68,
                          "badgeBg": "navy", "badgeOpacity": 0.8},
            }],
            "settings": {"video_height_pct": 80, "background_blur": True},
        })[2]
        fa_px = frame_pixels(out_path, at=1.0)
        la_px = frame_pixels(
            self.media_root / "renders" / latin["output_file"], at=1.0
        )
        self.assertNotEqual(fa_px, la_px)

    def test_master_title_and_style_render(self):
        """Master title with a custom style is drawn on the final output."""
        _, _, result = self._render({
            "aspect": "16:9",
            "master_title": "MASTER Q",
            "master_title_style": {"font": "ArchivoBlack",
                                   "fontSize": 110,
                                   "textColor": "#00FF00",
                                   "strokeWidth": 0, "shadow": False,
                                   "badgeBg": None},
            "clips": [{
                "video_id": self.video.id, "rank": 1, "start": 0.0,
                "end": 2.0, "title": "x",
            }],
            "settings": {"video_height_pct": 80, "background_blur": True},
        })
        out_path = self.media_root / "renders" / result["output_file"]
        px = frame_pixels(out_path, at=1.0)
        found = any(
            px[i] < 110 and px[i + 1] > 200 and px[i + 2] < 110
            for i in range(0, len(px) - 2, 3 * 4)
        )
        self.assertTrue(found, msg="no green master-title pixels found")

    def test_ducking_params_reach_output(self):
        """Custom ducking params are accepted and the render completes."""
        track = make_bgm_track(self.media_root)
        _, _, result = self._render({
            "clips": [{
                "video_id": self.video.id, "rank": 1, "start": 0.0,
                "end": 2.0, "title": "D",
            }],
            "settings": {"video_height_pct": 80, "background_blur": True,
                         "bgm_id": track.id, "bgm_volume": 0.4,
                         "ducking_threshold": 0.05, "ducking_ratio": 8},
        })
        out_path = self.media_root / "renders" / result["output_file"]
        facts = probe_media(out_path)
        self.assertTrue(facts["has_audio"])
        self.assertAlmostEqual(facts["duration"], 2.0, delta=0.6)

    def test_all_hd_job_progress_redistribution(self):
        """All-HD jobs skip the fetch phase (normalize starts at 0%)."""
        seen = []

        def spy(task_id, value):
            seen.append(round(float(value), 1))

        hd = synth_hd_section(self.hd_dir, duration=2.0)
        task = self._make_task()
        job = RenderJob.objects.create(task=task, payload={
            "clips": [{"rank": 1, "hd_file": str(hd), "title": "HD"}],
            "settings": {"video_height_pct": 80, "background_blur": True},
        })
        from apps.core import runner as runner_mod
        with mock.patch.object(runner_mod, "set_progress", side_effect=spy):
            run_render(task, job.pk)
        # run_render itself never flips status; verify via the spy values.
        self.assertTrue(seen)
        # early progress must start near 0 (not 5) for all-HD jobs
        self.assertLess(seen[0], 2.0, msg=f"first progress was {seen[0]}")
        # and it must have reached 100 during the run
        self.assertEqual(seen[-1], 100.0, msg=f"last progress was {seen[-1]}")
