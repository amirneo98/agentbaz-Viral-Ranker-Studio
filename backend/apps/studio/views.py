"""Clip-studio API views (v1.2).

Endpoints:
  - GET/POST   /api/presets/            style preset CRUD
  - GET/PUT/DELETE /api/presets/<id>/
  - GET/POST   /api/clips/              clip CRUD (list ordered by rank)
  - GET/PATCH/DELETE /api/clips/<id>/
  - POST       /api/clips/reorder/      rewrite rank fields from an id list
  - POST       /api/stream-info/        zero-wait preview metadata + stream URL
  - POST       /api/download-section/   async HD section download task
"""
from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import runner
from apps.core.models import Task
from apps.studio.models import Clip, StylePreset
from apps.studio.serializers import (
    ClipReorderSerializer,
    ClipSerializer,
    StylePresetSerializer,
)
from apps.videos.views import validate_fetch_url


class PresetListCreateView(APIView):
    """GET /api/presets/ — list; POST /api/presets/ — create."""

    def get(self, request):
        serializer = StylePresetSerializer(StylePreset.objects.all(), many=True)
        return Response({"presets": serializer.data}, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = StylePresetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class PresetDetailView(APIView):
    """GET/PUT/DELETE /api/presets/<id>/."""

    def get(self, request, preset_id):
        preset = get_object_or_404(StylePreset, pk=preset_id)
        serializer = StylePresetSerializer(preset)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, preset_id):
        preset = get_object_or_404(StylePreset, pk=preset_id)
        serializer = StylePresetSerializer(preset, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, preset_id):
        preset = get_object_or_404(StylePreset, pk=preset_id)
        preset.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ClipListCreateView(APIView):
    """GET /api/clips/ — list (rank order); POST /api/clips/ — create."""

    def get(self, request):
        serializer = ClipSerializer(Clip.objects.all(), many=True)
        return Response({"clips": serializer.data}, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = ClipSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ClipDetailView(APIView):
    """GET/PATCH/DELETE /api/clips/<uuid>/."""

    def get(self, request, clip_id):
        clip = get_object_or_404(Clip, pk=clip_id)
        serializer = ClipSerializer(clip)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, clip_id):
        clip = get_object_or_404(Clip, pk=clip_id)
        serializer = ClipSerializer(clip, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, clip_id):
        clip = get_object_or_404(Clip, pk=clip_id)
        # Best-effort file cleanup; the row must always be removed.
        if clip.hd_file and clip.hd_file.name:
            try:
                clip.hd_file.delete(save=False)
            except (FileNotFoundError, OSError, ValueError):
                pass
        clip.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ClipReorderView(APIView):
    """POST /api/clips/reorder/ — rewrite rank fields from an ordered id list."""

    def post(self, request):
        serializer = ClipReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data["ids"]

        existing = {
            str(c.pk): c for c in Clip.objects.filter(pk__in=ids)
        }
        missing = [str(i) for i in ids if str(i) not in existing]
        if missing:
            return Response(
                {"error": f"unknown clip ids: {', '.join(missing)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        for new_rank, clip_id in enumerate(ids, start=1):
            clip = existing[str(clip_id)]
            if clip.rank != new_rank:
                clip.rank = new_rank
                Clip.objects.filter(pk=clip.pk).update(rank=new_rank)

        serializer = ClipSerializer(
            Clip.objects.filter(pk__in=ids), many=True
        )
        return Response({"clips": serializer.data}, status=status.HTTP_200_OK)


class StreamInfoView(APIView):
    """POST /api/stream-info/ {url} — preview metadata + direct stream URL.

    Fast path (30s budget, two yt-dlp one-shot calls). Returns 400 for an
    invalid URL and 502 when yt-dlp cannot read the video at all.
    """

    def post(self, request):
        url, error = validate_fetch_url(request.data.get("url"))
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        try:
            from apps.videos.services.stream import StreamInfoError, get_stream_info
        except ImportError:
            return Response(
                {"error": "stream info service unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            info = get_stream_info(url)
        except StreamInfoError as exc:
            return Response(
                {"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY
            )
        return Response(info, status=status.HTTP_200_OK)


class DownloadSectionView(APIView):
    """POST /api/download-section/ {clip_id} — submit an HD section task.

    Returns 202 with a task id; poll GET /api/tasks/<task_id>/. Reuses the
    FETCH task type so the frontend's existing polling logic applies.
    """

    def post(self, request):
        clip_id = request.data.get("clip_id")
        if not clip_id:
            return Response(
                {"error": "clip_id is required"}, status=status.HTTP_400_BAD_REQUEST
            )
        clip = Clip.objects.filter(pk=clip_id).first()
        if clip is None:
            return Response(
                {"error": "unknown clip"}, status=status.HTTP_404_NOT_FOUND
            )

        try:
            from apps.videos.services.sections import run_download_section
        except ImportError:
            return Response(
                {"error": "download service unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Optimistic state flip: the frontend can show a spinner immediately.
        Clip.objects.filter(pk=clip.pk).exclude(
            hd_status=Clip.HD_PENDING
        ).update(hd_status=Clip.HD_PENDING)
        task = runner.submit(Task.TYPE_FETCH, run_download_section, str(clip.pk))
        return Response(
            {"task_id": str(task.id), "clip_id": str(clip.pk)},
            status=status.HTTP_202_ACCEPTED,
        )
