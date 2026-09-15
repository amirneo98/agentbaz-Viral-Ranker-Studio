"""Async task tracking shared by every pipeline."""
import uuid

from django.db import models


class Task(models.Model):
    """A background job (download or render) polled by the frontend."""

    TYPE_FETCH = "FETCH"
    TYPE_RENDER = "RENDER"
    TASK_TYPES = [
        (TYPE_FETCH, "Fetch"),
        (TYPE_RENDER, "Render"),
    ]

    STATUS_PENDING = "PENDING"
    STATUS_PROCESSING = "PROCESSING"
    STATUS_SUCCESS = "SUCCESS"
    STATUS_FAILED = "FAILED"
    STATUSES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_type = models.CharField(max_length=20, choices=TASK_TYPES)
    status = models.CharField(max_length=20, choices=STATUSES, default=STATUS_PENDING)
    progress = models.FloatField(default=0.0)
    error = models.TextField(blank=True, default="")
    result = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.task_type} {self.id} [{self.status}]"
