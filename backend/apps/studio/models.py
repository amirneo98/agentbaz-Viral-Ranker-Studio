"""Models for the clip-studio app (v1.2)."""
import uuid

from django.db import models


class StylePreset(models.Model):
    """A reusable style configuration (typography, colors, badge, effects).

    ``data`` is an opaque JSON blob for the frontend: font name, fontSize,
    colors, stroke, badge, blur, ducking, videoScale, etc. The backend only
    validates that it is a JSON object; shape ownership stays with the UI.
    """

    name = models.CharField(max_length=200, unique=True)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"StylePreset({self.name})"


class Clip(models.Model):
    """A ranked clip in the studio timeline.

    A Clip references its source by URL (stream preview) until the HD
    section download has been materialized into ``hd_file``.
    """

    HD_PENDING = "pending"
    HD_READY = "ready"
    HD_FAILED = "failed"
    HD_STATUSES = [
        (HD_PENDING, "Pending"),
        (HD_READY, "Ready"),
        (HD_FAILED, "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_url = models.URLField(max_length=2000)
    title = models.CharField(max_length=500, blank=True, default="")
    subtitle = models.CharField(max_length=500, blank=True, default="")
    rank = models.PositiveIntegerField()
    start_time = models.FloatField()
    end_time = models.FloatField()
    stream_url = models.URLField(max_length=2000, null=True, blank=True)
    stream_type = models.CharField(max_length=10, default="none")
    thumbnail = models.URLField(max_length=2000, null=True, blank=True)
    duration = models.FloatField(default=0.0)
    style = models.JSONField(default=dict, blank=True)
    volume = models.FloatField(default=1.0)
    hd_status = models.CharField(
        max_length=10, choices=HD_STATUSES, default=HD_PENDING
    )
    hd_file = models.FileField(upload_to="hd_clips/", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["rank", "created_at"]

    def __str__(self):
        return f"#{self.rank} {self.title or self.source_url}"

    @property
    def section_duration(self):
        return max(0.0, self.end_time - self.start_time)
