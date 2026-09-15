"""Serializers for the BGM library API."""
from rest_framework import serializers

from apps.bgm.models import BgmTrack


class BgmTrackSerializer(serializers.ModelSerializer):
    """API representation of a background-music track.

    The file URL is relative (``/media/bgm/...``) so the frontend can
    prefix its own origin.
    """

    id = serializers.IntegerField(read_only=True)
    url = serializers.SerializerMethodField()

    class Meta:
        model = BgmTrack
        fields = ["id", "name", "duration", "url"]
        read_only_fields = fields

    def get_url(self, obj):
        """Relative /media/bgm/... URL for the track file."""
        if not obj.file or not obj.file.name:
            return None
        return obj.file.url
