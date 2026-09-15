from django.db import models


class BgmTrack(models.Model):
    """A royalty-free background-music track selectable for renders."""

    name = models.CharField(max_length=200, unique=True)
    file = models.FileField(upload_to="bgm/")
    duration = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
