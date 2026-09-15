"""Serializers for the video library API."""
from rest_framework import serializers

from apps.videos.models import Video


class VideoSerializer(serializers.ModelSerializer):
    """API representation of a downloaded Video.

    File and thumbnail are exposed as *relative* URLs (``/media/...``) so the
    decoupled frontend can simply prefix its own origin when serving media.
    """

    id = serializers.IntegerField(read_only=True)
    thumbnail_url = serializers.SerializerMethodField()
    video_url = serializers.SerializerMethodField()

    class Meta:
        model = Video
        fields = [
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
        ]
        read_only_fields = fields

    def get_thumbnail_url(self, obj):
        """Relative /media/... URL for the thumbnail, or None."""
        if not obj.thumbnail or not obj.thumbnail.name:
            return None
        return obj.thumbnail.url

    def get_video_url(self, obj):
        """Relative /media/... URL for the video file."""
        if not obj.file or not obj.file.name:
            return None
        return obj.file.url
