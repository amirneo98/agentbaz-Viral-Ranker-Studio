"""Request validation for POST /api/render/."""
from rest_framework import serializers

from apps.bgm.models import BgmTrack
from apps.videos.models import Video

# Tolerance for float comparisons against source duration (seek drift,
# container rounding on remuxed downloads, etc.).
DURATION_EPSILON = 0.05

MAX_TITLE_CHARS = 60  # clip titles are truncated to this before drawtext


class ClipSerializer(serializers.Serializer):
    video_id = serializers.IntegerField(min_value=1)
    rank = serializers.IntegerField(min_value=1)
    start = serializers.FloatField(min_value=0.0)
    end = serializers.FloatField()
    title = serializers.CharField(
        required=False, allow_blank=True, max_length=500
    )

    def validate(self, attrs):
        start = attrs["start"]
        end = attrs["end"]
        if end is None or start >= end:
            raise serializers.ValidationError(
                {"end": "end must be strictly greater than start"}
            )

        try:
            video = Video.objects.get(pk=attrs["video_id"])
        except Video.DoesNotExist:
            raise serializers.ValidationError(
                {"video_id": f"video {attrs['video_id']} does not exist"}
            )

        if end > video.duration + DURATION_EPSILON:
            raise serializers.ValidationError(
                {
                    "end": (
                        f"end {end:.3f}s exceeds duration of video "
                        f"{video.pk} ({video.duration:.3f}s)"
                    )
                }
            )
        attrs["_video"] = video
        return attrs


class RenderSettingsSerializer(serializers.Serializer):
    video_height_pct = serializers.IntegerField(
        required=False, min_value=30, max_value=100, default=80
    )
    background_blur = serializers.BooleanField(required=False, default=True)
    bgm_id = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=1
    )
    bgm_volume = serializers.FloatField(
        required=False, min_value=0.0, max_value=1.0, default=0.4
    )


class RenderRequestSerializer(serializers.Serializer):
    master_title = serializers.CharField(
        required=False, allow_blank=True, max_length=200, default=""
    )
    clips = ClipSerializer(many=True)
    settings = RenderSettingsSerializer(required=False)

    def validate_clips(self, clips):
        if not clips:
            raise serializers.ValidationError("at least one clip is required")
        ranks = [c["rank"] for c in clips]
        if len(set(ranks)) != len(ranks):
            raise serializers.ValidationError(
                f"clip ranks must be unique (got {sorted(ranks)})"
            )
        return clips

    def validate(self, attrs):
        # BGM existence is checked here so the API returns a clean 400
        # before any Task row is created.
        settings_dict = attrs.get("settings") or {}
        bgm_id = settings_dict.get("bgm_id")
        if bgm_id is not None:
            try:
                BgmTrack.objects.get(pk=bgm_id)
            except BgmTrack.DoesNotExist:
                raise serializers.ValidationError(
                    {"settings": {"bgm_id": f"bgm track {bgm_id} does not exist"}}
                )
        if "settings" not in attrs:
            attrs["settings"] = {}
        return attrs


def build_payload(validated_data):
    """Convert serializer output into the dict stored on RenderJob.payload.

    Applies defaults + clamping so the pipeline never sees missing keys.
    """
    settings_in = validated_data.get("settings") or {}
    settings_out = {
        "video_height_pct": settings_in.get("video_height_pct", 80),
        "background_blur": settings_in.get("background_blur", True),
        "bgm_id": settings_in.get("bgm_id"),
        "bgm_volume": settings_in.get("bgm_volume", 0.4),
    }
    clips_out = []
    for clip in validated_data["clips"]:
        video = clip.get("_video") or Video.objects.get(pk=clip["video_id"])
        title = clip.get("title")
        if title is None or title == "":
            title = video.title
        clips_out.append(
            {
                "video_id": clip["video_id"],
                "rank": clip["rank"],
                "start": float(clip["start"]),
                "end": float(clip["end"]),
                "title": (title or "")[:MAX_TITLE_CHARS],
            }
        )
    return {
        "master_title": (validated_data.get("master_title") or ""),
        "clips": clips_out,
        "settings": settings_out,
    }
