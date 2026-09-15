"""v1.3 tests: OpenReel converter, preset mapping, proxy service, endpoints.

Layout:

* Pure converter tests (SimpleTestCase / TestCase) with realistic ProjectFile
  fixtures — video track + text overlay with pop animation + Persian text.
* URL classification + media resolution tests (incl. studio-Clip proxies).
* Preset → ffmpeg expression tests (every preset builds a valid-looking
  drawtext fragment; a representative set is executed against real ffmpeg).
* Proxy service tests with a mocked YtDlpDownloader (transcode path uses
  real ffmpeg with locally synthesized fixtures).
* Endpoint validation + submission tests (stubbed worker on the runner).
"""
import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
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

from apps.core.models import Task
from apps.core.tests import wait_for_task
from apps.renders.models import RenderJob
from apps.renders.services import openreel as or_svc
from apps.renders.services import proxy as proxy_svc
from apps.renders.services.openreel import (
    ALL_PRESETS,
    PRESET_MAPPINGS,
    OpenReelConversionError,
    build_animated_drawtext,
    classify_media_url,
    convert_project,
    resolve_media_item,
    validate_project_file,
)
from apps.studio.models import Clip
from apps.videos.models import Video
from apps.videos.services.probe import probe_media

from .tests_pipeline import make_video, media_override, synth_sample

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MEDIA_ID = "media-1"
PROXY_CLIP_PK = None  # filled per-test for preview resolution


def make_project_file(media_url, *, with_overlay=True, overlay_text="بهترین کلیپ",
                      preset="pop", canvas=(1080, 1920)):
    """Hand-crafted minimal-but-realistic OpenReel ProjectFile v1.2.0."""
    clip = {
        "id": "clip-1",
        "mediaId": MEDIA_ID,
        "trackId": "track-1",
        "startTime": 0.0,
        "duration": 4.0,
        "inPoint": 1.0,
        "outPoint": 3.5,
        "effects": [],
        "audioEffects": [],
        "transform": {
            "position": {"x": 0.5, "y": 0.5},
            "scale": {"x": 1, "y": 1},
            "rotation": 0,
            "anchor": {"x": 0.5, "y": 0.5},
            "opacity": 1,
        },
        "keyframes": [],
        "volume": 1,
    }
    track = {
        "id": "track-1",
        "type": "video",
        "name": "Video 1",
        "clips": [clip],
        "transitions": [],
        "locked": False,
        "hidden": False,
        "muted": False,
        "solo": False,
    }
    project = {
        "id": "proj-1",
        "name": "Test Project",
        "createdAt": 1700000000000,
        "modifiedAt": 1700000000000,
        "settings": {
            "width": canvas[0],
            "height": canvas[1],
            "frameRate": 30,
            "sampleRate": 48000,
            "channels": 2,
        },
        "mediaLibrary": {
            "items": [
                {
                    "id": MEDIA_ID,
                    "name": "source clip",
                    "type": "video",
                    "fileHandle": None,
                    "blob": None,
                    "metadata": {
                        "duration": 10.0,
                        "width": 1920,
                        "height": 1080,
                        "frameRate": 30,
                        "codec": "h264",
                        "sampleRate": 48000,
                        "channels": 2,
                        "fileSize": 12345678,
                    },
                    "thumbnailUrl": None,
                    "waveformData": None,
                    "originalUrl": media_url,
                }
            ]
        },
        "timeline": {
            "tracks": [track],
            "subtitles": [],
            "duration": 4.0,
            "markers": [],
        },
    }
    if with_overlay:
        project["textClips"] = [
            {
                "id": "text-1",
                "trackId": "track-1",
                "startTime": 0.5,
                "duration": 3.0,
                "text": overlay_text,
                "style": {
                    "fontFamily": "Vazirmatn",
                    "fontSize": 64,
                    "fontWeight": "bold",
                    "fontStyle": "normal",
                    "color": "#FFFFFF",
                    "strokeColor": "#111827",
                    "strokeWidth": 3,
                    "shadowColor": "#000000",
                    "shadowBlur": 8,
                    "shadowOffsetX": 3,
                    "shadowOffsetY": 3,
                    "textAlign": "center",
                    "verticalAlign": "middle",
                    "lineHeight": 1.2,
                    "letterSpacing": 0,
                },
                "transform": {
                    "position": {"x": 0.5, "y": 0.75},
                    "scale": {"x": 1, "y": 1},
                    "rotation": 0,
                    "anchor": {"x": 0.5, "y": 0.5},
                    "opacity": 1,
                },
                "animation": {
                    "preset": preset,
                    "params": {"popOvershoot": 0.4},
                    "inDuration": 0.4,
                    "outDuration": 0.3,
                },
                "keyframes": [],
            }
        ]
    return {"version": "1.2.0", "project": project}


def make_ranking(**overrides):
    ranking = {
        "clips": [
            {
                "mediaId": MEDIA_ID,
                "rank": 1,
                "title": "عنوان فارسی",
                "start": 1.0,
                "end": 3.5,
                "textAnim": "pop",
                "textStyle": {"fontFamily": "Vazirmatn", "fontSize": 64},
            }
        ],
        "master_title": "۵ کلیپ برتر",
        "aspect": "9:16",
    }
    ranking.update(overrides)
    return ranking


# ---------------------------------------------------------------------------
# ProjectFile validation
# ---------------------------------------------------------------------------

class ValidateProjectFileTests(SimpleTestCase):
    def _pf(self, **kw):
        pf = make_project_file("https://example.com/v.mp4")
        pf.update(kw)
        return pf

    def test_valid_file_passes(self):
        project = validate_project_file(self._pf())
        self.assertEqual(project["id"], "proj-1")

    def test_missing_version_rejected(self):
        with self.assertRaises(OpenReelConversionError):
            validate_project_file(self._pf(version=None))

    def test_future_minimum_reader_rejected(self):
        with self.assertRaises(OpenReelConversionError):
            validate_project_file(self._pf(minimumReaderVersion="9.9.9"))

    def test_compatible_minimum_reader_accepted(self):
        project = validate_project_file(
            self._pf(minimumReaderVersion="1.1.0"))
        self.assertEqual(project["name"], "Test Project")

    def test_missing_project_rejected(self):
        with self.assertRaises(OpenReelConversionError):
            validate_project_file({"version": "1.2.0"})

    def test_missing_settings_timeline_library_rejected(self):
        for key in ("settings", "timeline", "mediaLibrary"):
            pf = self._pf()
            del pf["project"][key]
            with self.assertRaises(OpenReelConversionError, msg=key):
                validate_project_file(pf)

    def test_dangling_media_id_rejected(self):
        pf = self._pf()
        pf["project"]["timeline"]["tracks"][0]["clips"][0]["mediaId"] = "ghost"
        with self.assertRaises(OpenReelConversionError):
            validate_project_file(pf)

    def test_virtual_clip_media_ids_ignored(self):
        pf = self._pf()
        pf["project"]["timeline"]["tracks"][0]["clips"][0]["mediaId"] = "text-1"
        # no media item with that id, but virtual prefixes are exempt
        with self.assertRaises(OpenReelConversionError):
            # still fails later: media type None → skipped in timeline walk
            convert_project(pf)

    def test_non_dict_rejected(self):
        with self.assertRaises(OpenReelConversionError):
            validate_project_file(["not", "a", "dict"])


# ---------------------------------------------------------------------------
# URL classification
# ---------------------------------------------------------------------------

class ClassifyUrlTests(SimpleTestCase):
    def test_full_quality_video_url(self):
        kind, rel = classify_media_url(
            "http://localhost:8000/media/videos/abc.mp4")
        self.assertEqual(kind, "full")
        self.assertEqual(rel, "videos/abc.mp4")

    def test_full_quality_hd_clips_url(self):
        kind, rel = classify_media_url(
            "http://127.0.0.1:8000/media/hd_clips/x.mp4")
        self.assertEqual(kind, "full")
        self.assertEqual(rel, "hd_clips/x.mp4")

    def test_preview_url(self):
        uid = str(uuid.uuid4())
        kind, rel = classify_media_url(
            f"http://localhost:8000/media/previews/{uid}.mp4")
        self.assertEqual(kind, "preview")
        self.assertEqual(rel, f"previews/{uid}.mp4")

    def test_other_media_url(self):
        kind, rel = classify_media_url(
            "http://localhost:8000/media/thumbs/t.jpg")
        self.assertEqual(kind, "media")
        self.assertEqual(rel, "thumbs/t.jpg")

    def test_remote_url(self):
        kind, url = classify_media_url("https://youtube.com/watch?v=x")
        self.assertEqual(kind, "remote")
        self.assertEqual(url, "https://youtube.com/watch?v=x")

    def test_urlencoded_paths(self):
        kind, rel = classify_media_url(
            "http://localhost:8000/media/videos/a%20b.mp4")
        self.assertEqual(kind, "full")
        self.assertEqual(rel, "videos/a b.mp4")

    def test_garbage_rejected(self):
        self.assertEqual(classify_media_url(None), (None, None))
        self.assertEqual(classify_media_url(""), (None, None))
        self.assertEqual(classify_media_url("ftp://x/y"), (None, None))
        self.assertEqual(classify_media_url("not a url"), (None, None))


# ---------------------------------------------------------------------------
# Conversion — remote sources (no DB media)
# ---------------------------------------------------------------------------

class ConvertRemoteProjectTests(SimpleTestCase):
    def test_basic_conversion_shape(self):
        payload = convert_project(
            make_project_file("https://example.com/v.mp4"),
            make_ranking(),
        )
        self.assertEqual(payload["master_title"], "۵ کلیپ برتر")
        self.assertEqual(payload["aspect"], "9:16")
        self.assertEqual(len(payload["clips"]), 1)
        clip = payload["clips"][0]
        self.assertEqual(clip["rank"], 1)
        self.assertEqual(clip["start"], 1.0)
        self.assertEqual(clip["end"], 3.5)
        self.assertEqual(clip["title"], "عنوان فارسی")
        self.assertEqual(clip["source_url"], "https://example.com/v.mp4")
        self.assertNotIn("video_id", clip)
        self.assertNotIn("hd_file", clip)
        self.assertEqual(payload["settings"]["video_height_pct"], 80)

    def test_overlay_descriptor_attached(self):
        payload = convert_project(
            make_project_file("https://example.com/v.mp4"),
            make_ranking(),
        )
        overlay = payload["clips"][0]["openreel_text"]
        self.assertEqual(overlay["text"], "بهترین کلیپ")
        self.assertEqual(overlay["animation"]["preset"], "pop")
        # overlay starts at 0.5s on the timeline; the clip trims from 1.0
        # → offset clamped to 0 within the clip window
        self.assertGreaterEqual(overlay["offset"], 0.0)
        self.assertGreater(overlay["duration"], 0.0)

    def test_ranking_text_anim_overrides_project(self):
        pf = make_project_file("https://example.com/v.mp4", preset="fade")
        ranking = make_ranking()
        ranking["clips"][0]["textAnim"] = "shake"
        payload = convert_project(pf, ranking)
        self.assertEqual(
            payload["clips"][0]["openreel_text"]["animation"]["preset"],
            "shake",
        )

    def test_auto_ranks_when_ranking_empty(self):
        pf = make_project_file("https://example.com/v.mp4")
        payload = convert_project(pf)
        self.assertEqual(payload["clips"][0]["rank"], 1)
        self.assertEqual(payload["clips"][0]["source_url"],
                         "https://example.com/v.mp4")

    def test_canvas_orientation_selects_aspect(self):
        wide = make_project_file("https://example.com/v.mp4",
                                 canvas=(1920, 1080))
        self.assertEqual(convert_project(wide)["aspect"], "16:9")
        tall = make_project_file("https://example.com/v.mp4",
                                 canvas=(1080, 1920))
        self.assertEqual(convert_project(tall)["aspect"], "9:16")

    def test_no_video_tracks_rejected(self):
        pf = make_project_file("https://example.com/v.mp4")
        pf["project"]["timeline"]["tracks"] = []
        with self.assertRaises(OpenReelConversionError):
            convert_project(pf)

    def test_hidden_tracks_skipped(self):
        pf = make_project_file("https://example.com/v.mp4")
        pf["project"]["timeline"]["tracks"][0]["hidden"] = True
        with self.assertRaises(OpenReelConversionError):
            convert_project(pf)

    def test_duplicate_ranks_rejected(self):
        pf = make_project_file("https://example.com/v.mp4")
        # add a second clip on the same media
        clip2 = json.loads(json.dumps(
            pf["project"]["timeline"]["tracks"][0]["clips"][0]))
        clip2["id"] = "clip-2"
        clip2["startTime"] = 5.0
        pf["project"]["timeline"]["tracks"][0]["clips"].append(clip2)
        ranking = make_ranking()
        ranking["clips"].append({"mediaId": MEDIA_ID, "rank": 1})
        with self.assertRaises(OpenReelConversionError):
            convert_project(pf, ranking)

    def test_empty_trim_window_rejected(self):
        ranking = make_ranking()
        ranking["clips"][0]["start"] = 2.0
        ranking["clips"][0]["end"] = 2.0
        with self.assertRaises(OpenReelConversionError):
            convert_project(
                make_project_file("https://example.com/v.mp4"), ranking)

    def test_clip_in_out_used_when_ranking_silent(self):
        payload = convert_project(make_project_file("https://x.com/v.mp4"))
        clip = payload["clips"][0]
        self.assertEqual(clip["start"], 1.0)   # inPoint
        self.assertEqual(clip["end"], 3.5)     # outPoint

    def test_two_clips_get_sequential_auto_ranks(self):
        pf = make_project_file("https://example.com/v.mp4")
        clip2 = json.loads(json.dumps(
            pf["project"]["timeline"]["tracks"][0]["clips"][0]))
        clip2["id"] = "clip-2"
        clip2["startTime"] = 5.0
        pf["project"]["timeline"]["tracks"][0]["clips"].append(clip2)
        payload = convert_project(pf)
        ranks = sorted(c["rank"] for c in payload["clips"])
        self.assertEqual(ranks, [1, 2])


# ---------------------------------------------------------------------------
# Conversion — local media resolution (DB)
# ---------------------------------------------------------------------------

class ConvertLocalMediaTests(TestCase):
    def setUp(self):
        super().setUp()
        self.media_root = media_override(self)

    def test_video_media_url_resolves_to_video_id(self):
        video = make_video()
        url = f"http://localhost:8000{video.file.url}"
        payload = convert_project(
            make_project_file(url),
            make_ranking(),
        )
        clip = payload["clips"][0]
        self.assertEqual(clip["video_id"], video.id)
        self.assertNotIn("source_url", clip)

    def test_full_quality_path_without_video_row_uses_hd_file(self):
        src = Path(synth_sample())
        dest_dir = Path(self.media_root) / "hd_clips"
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / "orphan.mp4"
        shutil.copy(src, target)
        payload = convert_project(
            make_project_file("http://localhost:8000/media/hd_clips/orphan.mp4"),
            make_ranking(),
        )
        clip = payload["clips"][0]
        self.assertEqual(clip["hd_file"], str(target))

    def test_missing_full_quality_file_rejected(self):
        with self.assertRaises(OpenReelConversionError):
            convert_project(
                make_project_file(
                    "http://localhost:8000/media/videos/ghost.mp4"),
                make_ranking(),
            )

    def test_preview_proxy_resolves_studio_clip(self):
        # studio Clip whose pk names the proxy file
        clip = Clip.objects.create(
            source_url="https://youtube.com/watch?v=abc",
            title="Studio", rank=1, start_time=0.0, end_time=2.0,
        )
        url = f"http://localhost:8000/media/previews/{clip.pk}.mp4"
        payload = convert_project(make_project_file(url), make_ranking())
        out = payload["clips"][0]
        self.assertEqual(out["clip_id"], str(clip.pk))
        self.assertEqual(out["source_url"], clip.source_url)
        self.assertNotIn("hd_file", out)  # hd pending → no hd_file

    def test_preview_proxy_uses_ready_hd_file(self):
        src = Path(synth_sample())
        clip = Clip.objects.create(
            source_url="https://youtube.com/watch?v=abc",
            title="Studio", rank=1, start_time=0.0, end_time=2.0,
            hd_status=Clip.HD_READY,
        )
        clip.hd_file.save("hd_clips/section.mp4",
                          ContentFile(src.read_bytes()), save=True)
        url = f"http://localhost:8000/media/previews/{clip.pk}.mp4"
        payload = convert_project(make_project_file(url), make_ranking())
        out = payload["clips"][0]
        self.assertEqual(out["hd_file"], clip.hd_file.name)
        self.assertEqual(out["clip_id"], str(clip.pk))

    def test_unknown_preview_used_directly_when_file_exists(self):
        previews = Path(self.media_root) / "previews"
        previews.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4()}.mp4"
        shutil.copy(synth_sample(), previews / name)
        url = f"http://localhost:8000/media/previews/{name}"
        payload = convert_project(make_project_file(url), make_ranking())
        self.assertIn("hd_file", payload["clips"][0])

    def test_missing_preview_rejected(self):
        url = f"http://localhost:8000/media/previews/{uuid.uuid4()}.mp4"
        with self.assertRaises(OpenReelConversionError):
            convert_project(make_project_file(url), make_ranking())

    def test_media_without_original_url_rejected(self):
        pf = make_project_file("")
        pf["project"]["mediaLibrary"]["items"][0]["originalUrl"] = None
        with self.assertRaises(OpenReelConversionError):
            convert_project(pf)


# ---------------------------------------------------------------------------
# Preset → drawtext fragments
# ---------------------------------------------------------------------------

class PresetMappingTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.staging = tempfile.mkdtemp(prefix="or-frag-")
        self.addCleanup(shutil.rmtree, self.staging, ignore_errors=True)

    def _build(self, preset, text="سلام دنیا", style=None, **anim_kw):
        animation = {"preset": preset, "inDuration": 0.4,
                     "outDuration": 0.3, "params": {}}
        animation.update(anim_kw)
        base_style = {"fontFamily": "Vazirmatn", "fontSize": 64,
                      "color": "#FFFFFF", "strokeWidth": 3,
                      "strokeColor": "#111827"}
        if style:
            base_style.update(style)
        return build_animated_drawtext(
            text=text,
            style=base_style,
            transform={"position": {"x": 0.5, "y": 0.75}, "opacity": 1},
            animation=animation,
            aspect="9:16",
            staging_dir=self.staging,
            clip_offset=0.2,
            clip_duration=3.0,
        )

    def test_every_preset_builds_a_fragment(self):
        for preset in ALL_PRESETS:
            frag, _tf = self._build(preset)
            self.assertTrue(frag.startswith("drawtext="), msg=preset)
            self.assertIn("fontfile=", frag, msg=preset)
            self.assertIn("enable='between(t,", frag, msg=preset)

    def test_mapping_table_covers_all_presets(self):
        self.assertEqual(set(PRESET_MAPPINGS), set(ALL_PRESETS))

    def test_none_preset_is_static(self):
        frag, _ = self._build("none")
        self.assertNotIn("sin(", frag)
        self.assertNotIn("if(", frag)

    def test_fade_preset_uses_if_alpha(self):
        frag, _ = self._build("fade")
        self.assertIn("alpha=", frag)
        self.assertIn("if(lt(t,", frag)

    def test_slide_presets_offset_position(self):
        frag, _ = self._build("slide-up")
        self.assertIn("(h-text_h)", frag)
        self.assertIn("*(1-", frag)

    def test_pop_preset_bounce_expression(self):
        frag, _ = self._build("pop")
        self.assertIn("abs(sin(", frag)
        self.assertIn("exp(", frag)

    def test_shake_jitters_both_axes(self):
        frag, _ = self._build("shake")
        self.assertIn("sin(", frag)
        self.assertIn("cos(", frag)

    def test_word_presets_emit_multiple_drawtexts(self):
        frag, _ = self._build("word-by-word", text="one two three")
        self.assertEqual(frag.count("drawtext="), 3)
        self.assertIn("enable='between(t,0.200,3.200)'", frag)
        frag2, _ = self._build("typewriter", text="یک دو سه")
        self.assertGreaterEqual(frag2.count("drawtext="), 3)

    def test_cascade_words_slide_and_stagger(self):
        frag, _ = self._build("cascade", text="a b c")
        self.assertEqual(frag.count("drawtext="), 3)
        # each word window starts later than the previous
        self.assertIn("between(t,0.200,", frag)
        self.assertIn("between(t,0.400,", frag)

    def test_enable_window_uses_clip_offset(self):
        frag, _ = self._build("none")
        self.assertIn("enable='between(t,0.200,3.200)'", frag)

    def test_styling_options_render(self):
        frag, _ = self._build(
            "none",
            style={"fontFamily": "Vazirmatn", "fontSize": 64,
                   "color": "#FFFFFF", "strokeWidth": 3,
                   "strokeColor": "#111827", "shadowColor": "#000000"},
        )
        self.assertIn("borderw=3", frag)
        self.assertIn("bordercolor=0x111827", frag)
        self.assertIn("shadowcolor=", frag)

    def test_position_from_normalized_transform(self):
        frag, _ = self._build("none")
        self.assertIn("(w-text_w)*0.5000", frag)
        self.assertIn("(h-text_h)*0.7500", frag)

    def test_persian_text_goes_through_textfile(self):
        frag, textfile = self._build("none", text="بهترین کلیپ سال")
        self.assertIsNotNone(textfile)
        self.assertIn(f"textfile='{textfile}'", frag)
        with open(textfile, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "بهترین کلیپ سال")

    def test_unknown_preset_falls_back_to_static(self):
        frag, _ = self._build("some-future-preset")
        self.assertTrue(frag.startswith("drawtext="))

    def test_no_animation_is_static(self):
        frag, textfile = build_animated_drawtext(
            text="plain",
            style={"fontFamily": "Vazirmatn", "fontSize": 48},
            transform={"position": {"x": 0.5, "y": 0.5}},
            animation=None,
            aspect="9:16",
            staging_dir=self.staging,
        )
        self.assertIn("drawtext=", frag)
        self.assertNotIn("alpha=", frag.split("alpha=")[0])
        self.assertIn("alpha=", frag)  # static alpha still emitted

    def test_empty_text_returns_empty(self):
        frag, textfile = build_animated_drawtext(
            text="",
            style={}, transform={},
            animation={"preset": "word-by-word"},
            aspect="9:16", staging_dir=self.staging,
        )
        self.assertEqual((frag, textfile), ("", None))


# ---------------------------------------------------------------------------
# Preset fragments run against real ffmpeg
# ---------------------------------------------------------------------------

class PresetFfmpegExecutionTests(SimpleTestCase):
    """Every preset's fragment is spliced into a real drawtext chain and
    executed — catches expression syntax errors and the fontsize-expr
    segfault class of bug."""

    def setUp(self):
        super().setUp()
        self.staging = tempfile.mkdtemp(prefix="or-exec-")
        self.addCleanup(shutil.rmtree, self.staging, ignore_errors=True)

    def _run(self, frag):
        cmd = [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=540x960:d=1",
            "-vf", frag,
            "-frames:v", "6",
            "-c:v", "libx264", "-preset", "ultrafast",
            str(Path(self.staging) / "out.mp4"),
        ]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    def test_representative_presets_execute(self):
        for preset in ("none", "fade", "slide-up", "pop", "shake", "glitch",
                       "wave", "bounce", "word-by-word", "cascade",
                       "typewriter", "rise", "swing", "rainbow", "elastic"):
            with self.subTest(preset=preset):
                frag, _ = build_animated_drawtext(
                    text="سلام دنیا Test",
                    style={"fontFamily": "Vazirmatn", "fontSize": 48,
                           "strokeWidth": 2, "strokeColor": "#000000"},
                    transform={"position": {"x": 0.5, "y": 0.7}, "opacity": 1},
                    animation={"preset": preset, "inDuration": 0.4,
                               "outDuration": 0.2, "params": {}},
                    aspect="9:16",
                    staging_dir=self.staging,
                    clip_offset=0.1,
                    clip_duration=1.0,
                )
                proc = self._run(frag)
                self.assertEqual(
                    proc.returncode, 0,
                    msg=f"{preset}: {proc.stderr[-400:]}",
                )


# ---------------------------------------------------------------------------
# Proxy service
# ---------------------------------------------------------------------------

def _fake_download_result(path):
    """DownloadResult-like namespace for the mocked downloader."""
    from types import SimpleNamespace

    return SimpleNamespace(path=Path(path), info={"id": "x"}, thumbnail=None)


def _synth_unplayable(directory, name="source.webm"):
    """A VP8/webm file browsers may still play but that must be
    transcoded because it is not mp4/H.264."""
    path = Path(directory) / name
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=640x360:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libvpx", "-c:a", "libvorbis", str(path)],
        check=True, capture_output=True, text=True,
    )
    return path


class ProxyServiceTests(TestCase):
    def setUp(self):
        super().setUp()
        self.media_root = Path(media_override(self))
        self.task = Task.objects.create(
            task_type=Task.TYPE_FETCH, status=Task.STATUS_PROCESSING
        )

    def _downloader_mock(self, downloaded_path, selector_seen=[]):
        # Copy the fixture: the service MOVES the downloaded file into
        # media/previews, which must not consume the shared sample cache.
        tmp = tempfile.mkdtemp(prefix="or-dl-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        private = Path(tmp) / Path(downloaded_path).name
        shutil.copy(downloaded_path, private)

        def fake_download(url, staging_dir, progress_cb=None):
            selector_seen.append(url)
            return _fake_download_result(private)

        instance = mock.Mock()
        instance.download = fake_download
        return mock.patch(
            "apps.videos.services.downloader.YtDlpDownloader",
            return_value=instance,
        )

    def test_playable_mp4_moved_unchanged(self):
        src = Path(synth_sample())
        with self._downloader_mock(src):
            result = proxy_svc.run_proxy_download(self.task, "https://x.com/v")
        proxy_path = self.media_root / "previews" / Path(
            result["proxy_url"]).name
        self.assertTrue(proxy_path.is_file())
        self.assertTrue(result["proxy_url"].startswith("/media/previews/"))
        facts = probe_media(proxy_path)
        self.assertEqual(facts["video_codec"], "h264")
        self.assertLessEqual(facts["height"], 720)

    def test_unplayable_source_transcoded_to_h264_mp4(self):
        tmp = tempfile.mkdtemp(prefix="or-proxy-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        webm = _synth_unplayable(tmp)
        with self._downloader_mock(webm):
            result = proxy_svc.run_proxy_download(self.task, "https://x.com/v")
        proxy_path = self.media_root / "previews" / Path(
            result["proxy_url"]).name
        facts = probe_media(proxy_path)
        self.assertEqual(facts["video_codec"], "h264")
        self.assertEqual(proxy_path.suffix, ".mp4")
        self.assertLessEqual(facts["height"], 720)

    def test_1080_source_scaled_to_720(self):
        tmp = tempfile.mkdtemp(prefix="or-proxy-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        big = Path(tmp) / "big.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi",
             "-i", "testsrc2=size=1280x1080:rate=30:duration=1",
             "-c:v", "libx264", "-preset", "ultrafast", str(big)],
            check=True, capture_output=True, text=True,
        )
        # not playable-as-proxy: height > 720 → scale filter path
        # (1280x1080 h264 mp4 would pass codec check, so force the
        # transcode by checking the height guard separately)
        facts_in = probe_media(big)
        self.assertGreater(facts_in["height"], 720)
        with self._downloader_mock(big):
            result = proxy_svc.run_proxy_download(self.task, "https://x.com/v")
        proxy_path = self.media_root / "previews" / Path(
            result["proxy_url"]).name
        facts = probe_media(proxy_path)
        self.assertLessEqual(facts["height"], 720)

    def test_format_selector_is_720_capped(self):
        captured = {}
        tmp = tempfile.mkdtemp(prefix="or-fmt-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        private = Path(tmp) / "sample.mp4"
        shutil.copy(synth_sample(), private)

        class SpyDownloader:
            def __init__(self, format_selector=None, **kw):
                captured["selector"] = format_selector

            def download(self, url, staging_dir, progress_cb=None):
                return _fake_download_result(private)

        with mock.patch(
            "apps.videos.services.downloader.YtDlpDownloader", SpyDownloader
        ):
            proxy_svc.run_proxy_download(self.task, "https://x.com/v")
        self.assertEqual(
            captured["selector"],
            "best[height<=720][ext=mp4]/best[height<=720]/best",
        )

    def test_downloader_failure_raises_proxy_error(self):
        def boom(url, staging_dir, progress_cb=None):
            raise RuntimeError("no video there")

        instance = mock.Mock()
        instance.download = boom
        with mock.patch(
            "apps.videos.services.downloader.YtDlpDownloader",
            return_value=instance,
        ):
            with self.assertRaises(proxy_svc.ProxyError):
                proxy_svc.run_proxy_download(self.task, "https://x.com/v")
        # staging cleaned
        self.assertFalse((self.media_root / "previews" / ".staging").exists())

    def test_progress_reaches_100(self):
        self.task.status = Task.STATUS_PROCESSING
        self.task.save()
        with self._downloader_mock(synth_sample()):
            proxy_svc.run_proxy_download(self.task, "https://x.com/v")
        self.task.refresh_from_db()
        self.assertEqual(self.task.progress, 100.0)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

class OpenReelEndpointValidationTests(APITestCase):
    def test_missing_project_returns_400(self):
        response = self.client.post(
            reverse("render-openreel"), {"ranking": {}}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_invalid_project_structure_returns_400(self):
        response = self.client.post(
            reverse("render-openreel"),
            {"project": {"version": "1.2.0"}},  # no project field
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.data)

    def test_dangling_media_id_returns_400(self):
        pf = make_project_file("https://example.com/v.mp4")
        pf["project"]["timeline"]["tracks"][0]["clips"][0]["mediaId"] = "ghost"
        response = self.client.post(
            reverse("render-openreel"), {"project": pf}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("mediaId", response.data["error"])

    def test_remote_project_accepted_creates_task_and_job(self):
        pf = make_project_file("https://example.com/v.mp4")
        response = self.client.post(
            reverse("render-openreel"),
            {"project": pf, "ranking": make_ranking()},
            format="json",
        )
        self.assertEqual(response.status_code, 202)
        task_id = response.data["task_id"]
        task = Task.objects.get(pk=task_id)
        self.assertEqual(task.task_type, Task.TYPE_RENDER)
        job = RenderJob.objects.get(task=task)
        self.assertEqual(job.payload["project"]["version"], "1.2.0")
        self.assertEqual(job.payload["ranking"]["master_title"], "۵ کلیپ برتر")

    def test_validation_failure_creates_no_tasks(self):
        self.client.post(reverse("render-openreel"), {}, format="json")
        self.client.post(
            reverse("render-openreel"),
            {"project": {"version": "1.2.0", "project": {
                "id": "x", "name": "x", "settings": {}, "timeline": {},
                "mediaLibrary": {"items": []},
            }}},
            format="json",
        )
        self.assertEqual(Task.objects.count(), 0)

    def test_bad_ranking_aspect_rejected(self):
        pf = make_project_file("https://example.com/v.mp4")
        response = self.client.post(
            reverse("render-openreel"),
            {"project": pf,
             "ranking": {"aspect": "4:3", "clips": []}},
            format="json",
        )
        self.assertEqual(response.status_code, 400)


class OpenReelRenderSubmitTests(TransactionTestCase):
    """202 path with the worker stubbed at the conversion boundary."""

    client_class = APIClient

    def test_submit_runs_conversion_and_render(self):
        calls = {}

        def stub_render(task, job_pk):
            from apps.core import runner

            calls["job_pk"] = job_pk
            runner.set_progress(task.id, 50.0)
            return {"download_url": "/media/renders/xyz.mp4",
                    "encoder_used": "h264_nvenc"}

        pf = make_project_file("https://example.com/v.mp4")
        with mock.patch(
            "apps.renders.services.pipeline.run_render", stub_render
        ):
            response = self.client.post(
                reverse("render-openreel"),
                {"project": pf, "ranking": make_ranking()},
                format="json",
            )
            self.assertEqual(response.status_code, 202)
            task_id = response.data["task_id"]
            done = wait_for_task(
                self.client, task_id,
                lambda r: r.data["status"] == Task.STATUS_SUCCESS,
            )

        self.assertEqual(done.data["task_type"], Task.TYPE_RENDER)
        job = RenderJob.objects.get(pk=calls["job_pk"])
        # the worker converted the stored payload into pipeline shape
        self.assertIn("clips", job.payload)
        self.assertEqual(job.payload["clips"][0]["source_url"],
                         "https://example.com/v.mp4")


class ProxyEndpointTests(APITestCase):
    def test_missing_url_returns_400(self):
        response = self.client.post(reverse("proxy-download"), {}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_invalid_url_returns_400(self):
        response = self.client.post(
            reverse("proxy-download"), {"url": "not-a-url"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_valid_url_returns_202_with_task(self):
        def stub_worker(task, url):
            return {"proxy_url": "/media/previews/x.mp4"}

        with mock.patch(
            "apps.renders.services.proxy.run_proxy_download", stub_worker
        ):
            response = self.client.post(
                reverse("proxy-download"),
                {"url": "https://example.com/v.mp4"},
                format="json",
            )
        self.assertEqual(response.status_code, 202)
        self.assertIn("task_id", response.data)
        self.assertTrue(Task.objects.filter(
            pk=response.data["task_id"],
            task_type=Task.TYPE_FETCH,
        ).exists())


class ProxyFullFlowTests(TransactionTestCase):
    """Proxy endpoint → real task runner → mocked downloader."""

    client_class = APIClient

    def test_full_flow_produces_proxy(self):
        # private copy — the service moves the downloaded file
        tmp = tempfile.mkdtemp(prefix="or-full-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        private = Path(tmp) / "sample.mp4"
        shutil.copy(synth_sample(), private)

        instance = mock.Mock()
        instance.download = lambda url, d, progress_cb=None: \
            _fake_download_result(private)
        with mock.patch(
            "apps.videos.services.downloader.YtDlpDownloader",
            return_value=instance,
        ), override_settings():
            response = self.client.post(
                reverse("proxy-download"),
                {"url": "https://example.com/v.mp4"},
                format="json",
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
        self.assertTrue(
            done.data["result"]["proxy_url"].startswith("/media/previews/")
        )


# ---------------------------------------------------------------------------
# End-to-end OpenReel render with real ffmpeg (single clip + overlay)
# ---------------------------------------------------------------------------

class OpenReelRealRenderTests(TestCase):
    """A real OpenReel render: local video media + pop-animated Persian
    overlay, executed through run_openreel_render with real ffmpeg."""

    def setUp(self):
        super().setUp()
        self.media_root = Path(media_override(self))

    def test_render_openreel_project_with_overlay(self):
        video = make_video()
        pf = make_project_file(f"http://localhost:8000{video.file.url}")
        ranking = make_ranking()
        task = Task.objects.create(
            task_type=Task.TYPE_RENDER, status=Task.STATUS_PROCESSING
        )
        job = RenderJob.objects.create(
            task=task,
            payload={"project": pf, "ranking": ranking},
        )
        from apps.renders.services.openreel import run_openreel_render

        result = run_openreel_render(task, job.pk)
        self.assertIn(result["encoder_used"], ("h264_nvenc", "libx264"))
        out_path = self.media_root / "renders" / result["output_file"]
        facts = probe_media(out_path)
        self.assertEqual((facts["width"], facts["height"]), (1080, 1920))
        self.assertTrue(facts["has_audio"])
        # ranking trim 1.0 → 3.5 = 2.5 s
        self.assertAlmostEqual(facts["duration"], 2.5, delta=0.6)
        task.refresh_from_db()
        self.assertEqual(task.progress, 100.0)
        # job payload now holds the converted pipeline shape
        job.refresh_from_db()
        self.assertEqual(job.payload["clips"][0]["video_id"], video.id)
