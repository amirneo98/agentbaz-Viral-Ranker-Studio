"""URLs render + preview views (v1.4).

POST /api/render/urls/        {master_title, aspect, clips[], settings}
                              → 202 {task_id}
GET  /api/render/urls/preview/?url=<enc> → 200 {proxy_url} | 404
"""
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import runner
from apps.core.models import Task
from apps.renders.models import RenderJob
from apps.renders.serializers import (
    UrlRenderRequestSerializer,
    build_urls_payload,
)
from apps.videos.views import validate_fetch_url


class UrlRenderView(APIView):
    """POST /api/render/urls/ — full-quality render from source links.

    Validates the request synchronously (400 on: empty clips, blank or
    non-http(s) source_url, end <= start, duplicate ranks, unknown bgm_id,
    invalid aspect) then schedules the download+render on the task runner,
    returning 202 {task_id} for polling at GET /api/tasks/<task_id>/.
    """

    def post(self, request):
        serializer = UrlRenderRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        payload = build_urls_payload(serializer.validated_data)

        task = Task.objects.create(task_type=Task.TYPE_RENDER)
        job = RenderJob.objects.create(task=task, payload=payload)

        from apps.renders.services.urls import run_urls_render

        runner.schedule_existing(task, run_urls_render, job.pk)
        return Response({"task_id": str(task.id)}, status=202)


class UrlPreviewView(APIView):
    """GET /api/render/urls/preview/?url=<enc> — cached 720p proxy lookup.

    Returns ``{"proxy_url": "/media/previews/cache/<hash>.mp4"}`` when a
    720p proxy for the URL is already cached, else 404.  Strictly
    read-only: this endpoint never downloads in the request thread.
    """

    def get(self, request):
        url, error = validate_fetch_url(request.query_params.get("url"))
        if error:
            return Response({"error": error}, status=400)

        from apps.renders.services.proxy import proxy_cache_path

        cached = proxy_cache_path(url)
        if cached is None:
            return Response(
                {"error": "no cached preview for this url"}, status=404
            )
        return Response(
            {"proxy_url": f"/media/previews/cache/{cached.name}"}
        )
