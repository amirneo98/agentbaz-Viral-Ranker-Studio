"""OpenReel render + proxy views (v1.3).

POST /api/render/openreel/  {project, ranking} → 202 {task_id}
POST /api/proxy/            {url}              → 202 {task_id, proxy_url_pending}
"""
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import runner
from apps.core.models import Task
from apps.renders.models import RenderJob
from apps.videos.views import validate_fetch_url


class OpenReelRankingClipSerializer(serializers.Serializer):
    """One entry of ranking.clips — every field optional except mediaId."""

    mediaId = serializers.CharField(max_length=128)
    rank = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, default=None
    )
    title = serializers.CharField(
        required=False, allow_blank=True, max_length=200, default=""
    )
    start = serializers.FloatField(
        required=False, allow_null=True, min_value=0.0, default=None
    )
    end = serializers.FloatField(required=False, allow_null=True, default=None)
    textAnim = serializers.CharField(
        required=False, allow_blank=True, max_length=64, default=""
    )
    textStyle = serializers.DictField(
        required=False, allow_null=True, default=None
    )


class OpenReelRankingSerializer(serializers.Serializer):
    clips = OpenReelRankingClipSerializer(many=True, required=False,
                                          default=list)
    master_title = serializers.CharField(
        required=False, allow_blank=True, max_length=200, default=""
    )
    aspect = serializers.ChoiceField(
        required=False, allow_blank=True, choices=["9:16", "16:9"], default=""
    )


class OpenReelRenderSerializer(serializers.Serializer):
    project = serializers.DictField()
    ranking = OpenReelRankingSerializer(required=False)


class OpenReelRenderView(APIView):
    """POST /api/render/openreel/ — render an OpenReel ProjectFile.

    Validates the ProjectFile structure synchronously (400 on structural
    errors: missing fields, dangling mediaIds) then submits the conversion
    + render to the task runner, returning 202 {task_id} for polling at
    GET /api/tasks/<task_id>/.
    """

    def post(self, request):
        serializer = OpenReelRenderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = serializer.validated_data["project"]
        ranking = serializer.validated_data.get("ranking") or {}

        # Structural validation up front so obvious breakage returns 400
        # before any Task row is created. Media existence is also checked
        # here (local /media/ references must exist).
        from apps.renders.services.openreel import (
            OpenReelConversionError,
            convert_project,
        )

        try:
            convert_project(project, ranking)
        except OpenReelConversionError as exc:
            return Response({"error": str(exc)}, status=400)

        task = Task.objects.create(task_type=Task.TYPE_RENDER)
        payload = {"project": project, "ranking": dict(ranking)}
        job = RenderJob.objects.create(task=task, payload=payload)

        from apps.renders.services.openreel import run_openreel_render

        runner.schedule_existing(task, run_openreel_render, job.pk)
        return Response({"task_id": str(task.id)}, status=202)


class ProxyDownloadView(APIView):
    """POST /api/proxy/ {url} — async 720p H.264 proxy download.

    Returns 202 {task_id}; poll GET /api/tasks/<task_id>/ — the result
    carries ``{"proxy_url": "/media/previews/<uuid>.mp4", ...}`` once the
    download finished. Served under /media/previews/ by the existing
    media route.
    """

    def post(self, request):
        url, error = validate_fetch_url(request.data.get("url"))
        if error:
            return Response({"error": error}, status=400)
        try:
            from apps.renders.services.proxy import run_proxy_download
        except ImportError:
            return Response(
                {"error": "proxy service unavailable"},
                status=503,
            )
        task = runner.submit(Task.TYPE_FETCH, run_proxy_download, url)
        return Response({"task_id": str(task.id)}, status=202)
