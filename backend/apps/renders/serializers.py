"""Request validation for POST /api/render/ (v1.2).

v1.1 shape (video_id clips + the four original settings) validates exactly
as before; v1.2 additions (aspect, per-clip style/volume/hd_file/subtitle,
master_title_style, blur_sigma, ducking params, video_scale_pct) are
accepted and passed through to the stored payload.  ``build_payload``
only emits the new keys when they were actually supplied, so stored v1.1
payloads (and their exact settings dict) are unchanged.
"""
from urllib.parse import urlsplit

from rest_framework import serializers

from apps.bgm.models import BgmTrack
from apps.videos.models import Video

# Tolerance for float comparisons against source duration (seek drift,
# container rounding on remuxed downloads, etc.).
DURATION_EPSILON = 0.05

MAX_TITLE_CHARS = 60  # clip titles are truncated to this before drawtext
MAX_SUBTITLE_CHARS = 80


class ClipSerializer(serializers.Serializer):
    video_id = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, default=None
    )
    rank = serializers.IntegerField(min_value=1)
    start = serializers.FloatField(required=False, allow_null=True, default=None)
    end = serializers.FloatField(required=False, allow_null=True, default=None)
    title = serializers.CharField(
        required=False, allow_blank=True, max_length=500
    )
    # ---- v1.2 passthrough fields ----
    # studio Clip pks are UUIDs; accept any string (or legacy int) id
    clip_id = serializers.CharField(
        required=False, allow_null=True, allow_blank=True,
        max_length=64, default=None,
    )
    subtitle = serializers.CharField(
        required=False, allow_blank=True, max_length=500
    )
    style = serializers.DictField(required=False, allow_null=True, default=None)
    volume = serializers.FloatField(
        required=False, allow_null=True, min_value=0.0, max_value=3.0,
        default=None,
    )
    hd_file = serializers.CharField(
        required=False, allow_blank=True, max_length=1000, default=""
    )

    def validate(self, attrs):
        hd_file = (attrs.get("hd_file") or "").strip()
        video_id = attrs.get("video_id")
        clip_id = (attrs.get("clip_id") or "").strip() or None

        # Resolve a studio clip_id into its HD file / metadata.
        if clip_id is not None:
            from apps.studio.models import Clip

            try:
                studio_clip = Clip.objects.get(pk=clip_id)
            except (Clip.DoesNotExist, ValueError, TypeError):
                raise serializers.ValidationError(
                    {"clip_id": f"studio clip {clip_id} does not exist"}
                )
            attrs["_studio_clip"] = studio_clip
            # explicit hd_file wins, else use the fetched HD section
            if not hd_file and studio_clip.hd_status == "ready" and studio_clip.hd_file:
                # hd_file is a FileField — store the relative name, not FieldFile
                hd_file = studio_clip.hd_file.name
                attrs["hd_file"] = hd_file
            # fill missing editable fields from the stored clip
            if attrs.get("start") is None:
                attrs["start"] = studio_clip.start_time
            if attrs.get("end") is None:
                attrs["end"] = studio_clip.end_time
            if attrs.get("title") in (None, ""):
                attrs["title"] = studio_clip.title
            if attrs.get("subtitle") in (None, ""):
                attrs["subtitle"] = studio_clip.subtitle
            if attrs.get("style") is None:
                attrs["style"] = studio_clip.style or None
            if attrs.get("volume") is None:
                attrs["volume"] = studio_clip.volume

        if video_id is None and not hd_file and clip_id is None:
            raise serializers.ValidationError(
                {"video_id": "clip needs video_id, clip_id, or hd_file"}
            )

        if video_id is None:
            # HD-section / clip_id-only clip: no video trim validation
            # possible here (pipeline downloads or uses the section file).
            return attrs

        start = attrs.get("start")
        end = attrs.get("end")
        if start is None or end is None:
            raise serializers.ValidationError(
                {"start": "video_id clips require start and end"}
            )
        if end is None or start >= end:
            raise serializers.ValidationError(
                {"end": "end must be strictly greater than start"}
            )

        try:
            video = Video.objects.get(pk=video_id)
        except Video.DoesNotExist:
            raise serializers.ValidationError(
                {"video_id": f"video {video_id} does not exist"}
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
        required=False, min_value=30, max_value=100
    )
    background_blur = serializers.BooleanField(required=False, default=True)
    bgm_id = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=1
    )
    bgm_volume = serializers.FloatField(
        required=False, min_value=0.0, max_value=1.0, default=0.4
    )
    # ---- v1.2 ----
    video_scale_pct = serializers.IntegerField(
        required=False, min_value=30, max_value=100
    )
    blur_sigma = serializers.FloatField(
        required=False, min_value=0.1, max_value=100.0
    )
    ducking_threshold = serializers.FloatField(
        required=False, min_value=0.0001, max_value=1.0
    )
    ducking_ratio = serializers.FloatField(
        required=False, min_value=1.0, max_value=20.0
    )
    aspect = serializers.ChoiceField(
        required=False, choices=["9:16", "16:9"]
    )


class RenderRequestSerializer(serializers.Serializer):
    master_title = serializers.CharField(
        required=False, allow_blank=True, max_length=200, default=""
    )
    clips = ClipSerializer(many=True)
    settings = RenderSettingsSerializer(required=False)
    # ---- v1.2 ----
    aspect = serializers.ChoiceField(
        required=False, choices=["9:16", "16:9"]
    )
    master_title_style = serializers.DictField(
        required=False, allow_null=True, default=None
    )

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

    v1.1 keys are always emitted with v1.1 defaults; v1.2 keys are emitted
    only when supplied so stored payloads (and existing consumers/tests)
    keep seeing the exact v1.1 shape for v1.1 requests.
    """
    settings_in = validated_data.get("settings") or {}
    settings_out = {
        "video_height_pct": settings_in.get(
            "video_height_pct",
            settings_in.get("video_scale_pct", 80),
        ),
        "background_blur": settings_in.get("background_blur", True),
        "bgm_id": settings_in.get("bgm_id"),
        "bgm_volume": settings_in.get("bgm_volume", 0.4),
    }
    # v1.2 settings — only when explicitly supplied.
    if settings_in.get("video_scale_pct") is not None:
        settings_out["video_scale_pct"] = settings_in["video_scale_pct"]
    if settings_in.get("blur_sigma") is not None:
        settings_out["blur_sigma"] = float(settings_in["blur_sigma"])
    if settings_in.get("ducking_threshold") is not None:
        settings_out["ducking_threshold"] = float(
            settings_in["ducking_threshold"]
        )
    if settings_in.get("ducking_ratio") is not None:
        settings_out["ducking_ratio"] = float(settings_in["ducking_ratio"])
    if settings_in.get("aspect"):
        settings_out["aspect"] = settings_in["aspect"]

    clips_out = []
    for clip in validated_data["clips"]:
        video = clip.get("_video")
        if clip.get("video_id") is not None:
            video = video or Video.objects.get(pk=clip["video_id"])
        title = clip.get("title")
        if (title is None or title == "") and video is not None:
            title = video.title
        entry = {
            "rank": clip["rank"],
            "title": (title or "")[:MAX_TITLE_CHARS],
        }
        if clip.get("video_id") is not None:
            entry["video_id"] = clip["video_id"]
            entry["start"] = float(clip["start"])
            entry["end"] = float(clip["end"])
        if clip.get("clip_id") is not None:
            entry["clip_id"] = clip["clip_id"]
            # clip_id clips carry their own trim window (used when the
            # pipeline falls back to a full source download)
            if clip.get("start") is not None:
                entry["start"] = float(clip["start"])
            if clip.get("end") is not None:
                entry["end"] = float(clip["end"])
            studio_clip = clip.get("_studio_clip")
            if studio_clip is not None:
                entry["source_url"] = studio_clip.source_url
                if not entry.get("hd_file") and studio_clip.hd_status == "ready" \
                        and studio_clip.hd_file:
                    entry["hd_file"] = studio_clip.hd_file.name
        if clip.get("hd_file"):
            entry["hd_file"] = clip["hd_file"]
        if clip.get("subtitle"):
            entry["subtitle"] = clip["subtitle"][:MAX_SUBTITLE_CHARS]
        if clip.get("style") is not None:
            entry["style"] = clip["style"]
        if clip.get("volume") is not None:
            entry["volume"] = float(clip["volume"])
        clips_out.append(entry)

    payload = {
        "master_title": (validated_data.get("master_title") or ""),
        "clips": clips_out,
        "settings": settings_out,
    }
    if validated_data.get("aspect"):
        payload["aspect"] = validated_data["aspect"]
    if validated_data.get("master_title_style") is not None:
        payload["master_title_style"] = validated_data["master_title_style"]
    return payload


# ---------------------------------------------------------------------------
# POST /api/render/urls/ — full-quality render straight from source links
# ---------------------------------------------------------------------------

#: Sources are downloaded server-side with the default (best quality)
#: format selector; a couple of sane caps guard against pathological input.
MAX_SOURCE_URL_LENGTH = 2000
MAX_URL_CLIPS = 50


def _clean_source_url(url):
    """Validate one clip source_url; returns (url, error_message)."""
    if not isinstance(url, str):
        return None, "source_url is required"
    url = url.strip()
    if not url:
        return None, "source_url must not be blank"
    if len(url) > MAX_SOURCE_URL_LENGTH:
        return None, (
            f"source_url must be at most {MAX_SOURCE_URL_LENGTH} characters"
        )
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None, "source_url must be an absolute http:// or https:// URL"
    return url, None


class UrlClipSerializer(serializers.Serializer):
    """One ranked clip referenced by its remote source URL."""

    source_url = serializers.CharField(
        required=False, allow_blank=True, max_length=MAX_SOURCE_URL_LENGTH
    )
    rank = serializers.IntegerField(min_value=1)
    start = serializers.FloatField(
        required=False, allow_null=True, min_value=0.0, default=None
    )
    end = serializers.FloatField(required=False, allow_null=True, default=None)
    title = serializers.CharField(
        required=False, allow_blank=True, max_length=500, default=""
    )
    subtitle = serializers.CharField(
        required=False, allow_blank=True, max_length=500, default=""
    )
    style = serializers.DictField(required=False, allow_null=True, default=None)
    volume = serializers.FloatField(
        required=False, allow_null=True, min_value=0.0, max_value=3.0,
        default=None,
    )

    def validate(self, attrs):
        url, error = _clean_source_url(attrs.get("source_url"))
        if error:
            raise serializers.ValidationError({"source_url": error})
        attrs["source_url"] = url

        start = attrs.get("start")
        end = attrs.get("end")
        if start is None or end is None:
            raise serializers.ValidationError(
                {"start": "source_url clips require start and end"}
            )
        if end <= start:
            raise serializers.ValidationError(
                {"end": "end must be strictly greater than start"}
            )
        return attrs


class UrlRenderRequestSerializer(serializers.Serializer):
    """Request shape for POST /api/render/urls/."""

    master_title = serializers.CharField(
        required=False, allow_blank=True, max_length=200, default=""
    )
    aspect = serializers.ChoiceField(
        required=False, choices=["9:16", "16:9"], default="9:16"
    )
    clips = UrlClipSerializer(many=True)
    settings = RenderSettingsSerializer(required=False)

    def validate_clips(self, clips):
        if not clips:
            raise serializers.ValidationError("at least one clip is required")
        if len(clips) > MAX_URL_CLIPS:
            raise serializers.ValidationError(
                f"at most {MAX_URL_CLIPS} clips are allowed per render"
            )
        ranks = [c["rank"] for c in clips]
        if len(set(ranks)) != len(ranks):
            raise serializers.ValidationError(
                f"clip ranks must be unique (got {sorted(ranks)})"
            )
        return clips

    def validate(self, attrs):
        settings_dict = attrs.get("settings") or {}
        bgm_id = settings_dict.get("bgm_id")
        if bgm_id is not None:
            try:
                BgmTrack.objects.get(pk=bgm_id)
            except BgmTrack.DoesNotExist:
                raise serializers.ValidationError(
                    {"settings": {"bgm_id": f"bgm track {bgm_id} does not exist"}}
                )
        return attrs


def build_urls_payload(validated_data):
    """Convert URL-render serializer output into the stored job payload.

    Mirrors ``build_payload`` (same defaults/clamping) but every clip
    carries ``source_url`` + its trim window instead of ``video_id``; the
    worker resolves URLs to full-quality files before rendering.
    """
    settings_in = validated_data.get("settings") or {}
    settings_out = {
        "video_height_pct": settings_in.get(
            "video_height_pct",
            settings_in.get("video_scale_pct", 80),
        ),
        "background_blur": settings_in.get("background_blur", True),
        "bgm_id": settings_in.get("bgm_id"),
        "bgm_volume": settings_in.get("bgm_volume", 0.4),
    }
    if settings_in.get("video_scale_pct") is not None:
        settings_out["video_scale_pct"] = settings_in["video_scale_pct"]
    if settings_in.get("blur_sigma") is not None:
        settings_out["blur_sigma"] = float(settings_in["blur_sigma"])
    if settings_in.get("ducking_threshold") is not None:
        settings_out["ducking_threshold"] = float(
            settings_in["ducking_threshold"]
        )
    if settings_in.get("ducking_ratio") is not None:
        settings_out["ducking_ratio"] = float(settings_in["ducking_ratio"])
    if settings_in.get("aspect"):
        settings_out["aspect"] = settings_in["aspect"]

    clips_out = []
    for clip in validated_data["clips"]:
        entry = {
            "rank": clip["rank"],
            "source_url": clip["source_url"],
            "start": float(clip["start"]),
            "end": float(clip["end"]),
            "title": (clip.get("title") or "")[:MAX_TITLE_CHARS],
        }
        if clip.get("subtitle"):
            entry["subtitle"] = clip["subtitle"][:MAX_SUBTITLE_CHARS]
        if clip.get("style") is not None:
            entry["style"] = clip["style"]
        if clip.get("volume") is not None:
            entry["volume"] = float(clip["volume"])
        clips_out.append(entry)

    return {
        "master_title": (validated_data.get("master_title") or ""),
        "aspect": validated_data["aspect"],
        "clips": clips_out,
        "settings": settings_out,
    }
