from django.db import models

from apps.core.models import Task


class RenderJob(models.Model):
    """One compilation render, created when POST /api/render/ is accepted."""

    task = models.OneToOneField(
        Task, on_delete=models.CASCADE, primary_key=True, related_name="render_job"
    )
    payload = models.JSONField()
    output_file = models.CharField(max_length=500, blank=True, default="")
    encoder_used = models.CharField(max_length=20, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"RenderJob {self.task_id}"
