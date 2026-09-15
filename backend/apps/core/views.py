"""Task polling and health API views.

``TaskDetailView`` lets the frontend follow a background download or render
from submission (202 with a task id) through to completion, by polling
``GET /api/tasks/<task_id>/``. ``HealthView`` is the liveness probe used by
the docker-compose healthcheck.
"""
from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Task
from apps.core.serializers import TaskSerializer


class TaskDetailView(APIView):
    """GET /api/tasks/<task_id>/ — poll status, progress, error and result."""

    def get(self, request, task_id):
        """Return the full Task state, or 404 for an unknown id."""
        task = get_object_or_404(Task, pk=task_id)
        serializer = TaskSerializer(task)
        return Response(serializer.data, status=status.HTTP_200_OK)


class HealthView(APIView):
    """GET /api/health/ — liveness probe used by docker-compose."""

    def get(self, request):
        return Response({"status": "ok"})
