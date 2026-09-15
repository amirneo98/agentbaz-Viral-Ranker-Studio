"""Serializers for the clip-studio API (v1.2)."""
from rest_framework import serializers

from apps.studio.models import Clip, StylePreset


class StylePresetSerializer(serializers.ModelSerializer):
    """CRUD representation of a style preset.

    ``data`` must be a JSON object (dict) but is otherwise opaque: the
    frontend owns the schema (font, fontSize, colors, stroke, badge, blur,
    ducking, videoScale, ...).
    """

    id = serializers.IntegerField(read_only=True)

    class Meta:
        model = StylePreset
        fields = ["id", "name", "data", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("name must not be blank")
        return value

    def validate_data(self, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("data must be a JSON object")
        return value


class ClipSerializer(serializers.ModelSerializer):
    """CRUD representation of a timeline clip.

    Validation rules (v1.2 spec):
      - ``start_time`` >= 0
      - ``end_time`` > ``start_time`` with at least a 1 second gap
      - ``rank`` is a positive integer
      - ``volume`` within [0, 2]
    """

    id = serializers.UUIDField(read_only=True)
    hd_file_url = serializers.SerializerMethodField()

    class Meta:
        model = Clip
        fields = [
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
        ]
        read_only_fields = ["id", "stream_type", "hd_status", "hd_file_url", "created_at"]

    def get_hd_file_url(self, obj):
        if obj.hd_file and obj.hd_file.name:
            return obj.hd_file.url
        return None

    # -- field validation ---------------------------------------------------

    def validate_start_time(self, value):
        if value is None or value < 0:
            raise serializers.ValidationError("start_time must be >= 0")
        return value

    def validate_end_time(self, value):
        if value is None:
            raise serializers.ValidationError("end_time is required")
        return value

    def validate_rank(self, value):
        if value is None or value < 1:
            raise serializers.ValidationError("rank must be a positive integer")
        return value

    def validate_volume(self, value):
        if value is None or not (0 <= value <= 2):
            raise serializers.ValidationError("volume must be between 0 and 2")
        return value

    def validate_style(self, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("style must be a JSON object")
        return value

    # -- object-level validation ---------------------------------------------

    def validate(self, attrs):
        # PATCH may update only one of the two bounds; compare against the
        # instance's current value for the missing one.
        start = attrs.get(
            "start_time", getattr(self.instance, "start_time", None)
        )
        end = attrs.get("end_time", getattr(self.instance, "end_time", None))
        if start is not None and end is not None:
            if end <= start:
                raise serializers.ValidationError(
                    "end_time must be greater than start_time"
                )
            if end - start < 1.0:
                raise serializers.ValidationError(
                    "end_time and start_time must be at least 1 second apart"
                )
        return attrs


class ClipReorderSerializer(serializers.Serializer):
    """Payload for POST /api/clips/reorder/ — ``{"ids": [...]}``."""

    ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
        min_length=1,
    )

    def validate_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("ids must not contain duplicates")
        return value
