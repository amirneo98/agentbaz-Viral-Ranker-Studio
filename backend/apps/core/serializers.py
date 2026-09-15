"""Serializers for the task-tracking API."""
from rest_framework import serializers

from apps.core.models import Task


class TaskSerializer(serializers.ModelSerializer):
    """Full representation of a background Task for polling clients.

    ``result`` is whatever JSON the worker stored: FETCH tasks carry the
    video info (including relative ``/media/...`` URLs) and RENDER tasks
    carry ``download_url``/``preview_url``/``encoder_used``.
    """

    id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Task
        fields = [
            "id",
            "task_type",
            "status",
            "progress",
            "error",
            "result",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
