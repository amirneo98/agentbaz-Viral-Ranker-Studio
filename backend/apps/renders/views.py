"""Render API views.

POST /api/render/ validates the request, creates the Task + RenderJob pair
and schedules the render pipeline on the in-process executor, returning
202 with the task id for polling.
"""
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import runner
from apps.core.models import Task
from apps.renders.models import RenderJob
from apps.renders.serializers import (
    RenderRequestSerializer,
    build_payload,
)
from apps.renders.services.pipeline import run_render


class RenderView(APIView):
    """POST /api/render/ — start an asynchronous compilation render."""

    def post(self, request):
        serializer = RenderRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        payload = build_payload(serializer.validated_data)

        task = Task.objects.create(task_type=Task.TYPE_RENDER)
        job = RenderJob.objects.create(task=task, payload=payload)
        runner.schedule_existing(task, run_render, job.pk)

        return Response({"task_id": str(task.id)}, status=202)
