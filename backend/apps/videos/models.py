from django.db import models


class Video(models.Model):
    """A downloaded source video available to the ranking timeline."""

    source_url = models.URLField(max_length=2000)
    title = models.CharField(max_length=500)
    duration = models.FloatField(help_text="Duration in seconds (ffprobe).")
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    has_audio = models.BooleanField(default=True)
    thumbnail = models.FileField(upload_to="thumbs/", null=True, blank=True)
    file = models.FileField(upload_to="videos/")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.duration:.1f}s)"

    @property
    def filename(self):
        return self.file.name.rsplit("/", 1)[-1] if self.file.name else ""
