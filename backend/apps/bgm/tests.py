"""Tests for the BGM library API."""
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.bgm.models import BgmTrack

BGM_FIELDS = {"id", "name", "duration", "url"}


def media_override(testcase):
    """Point MEDIA_ROOT at a per-test temp dir and clean it up afterwards."""
    import tempfile

    media = tempfile.TemporaryDirectory()
    testcase.addCleanup(media.cleanup)
    override = override_settings(MEDIA_ROOT=media.name)
    override.enable()
    testcase.addCleanup(override.disable)
    return media.name


def make_track(name="Sunrise Loop", duration=95.0, filename="sunrise.mp3"):
    """Create a BgmTrack row backed by a real file in MEDIA_ROOT.

    ``FileField.save`` prefixes names with the field's ``upload_to``
    (``bgm/``), so a bare filename is passed on purpose.
    """
    track = BgmTrack(name=name, duration=duration)
    track.file.save(filename, ContentFile(b"fake-audio-bytes"), save=False)
    track.save()
    return track


class BgmListTests(TestCase):
    """GET /api/bgm/"""

    def setUp(self):
        super().setUp()
        self.media_root = media_override(self)

    def test_empty_list(self):
        response = self.client.get(reverse("bgm-list"), format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"bgm": []})

    def test_lists_tracks_with_relative_urls(self):
        first = make_track(name="Alpha Loop")
        second = make_track(
            name="Beta Loop", duration=42.0, filename="beta.mp3"
        )

        response = self.client.get(reverse("bgm-list"), format="json")
        self.assertEqual(response.status_code, 200)
        tracks = response.data["bgm"]
        self.assertEqual(len(tracks), 2)
        for item in tracks:
            self.assertEqual(set(item), BGM_FIELDS)
            self.assertTrue(item["url"].startswith("/media/bgm/"))

        by_name = {t["name"]: t for t in tracks}
        self.assertEqual(by_name["Alpha Loop"]["duration"], 95.0)
        self.assertEqual(by_name["Beta Loop"]["duration"], 42.0)
        # Meta.ordering is ["name"] — the API should reflect it.
        self.assertEqual([t["name"] for t in tracks], ["Alpha Loop", "Beta Loop"])
        self.assertEqual(by_name["Alpha Loop"]["url"], "/media/bgm/sunrise.mp3")
        self.assertEqual(by_name["Beta Loop"]["url"], "/media/bgm/beta.mp3")
        self.assertEqual(by_name["Alpha Loop"]["id"], first.id)
        self.assertEqual(by_name["Beta Loop"]["id"], second.id)
