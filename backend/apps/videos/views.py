"""Video ingestion API views.

``FetchView`` accepts a URL, validates it and hands the actual download to
the background runner, returning 202 with a task id immediately. The video
library views list, inspect and delete downloaded videos (deleting both the
database row and its files from storage).
"""
from urllib.parse import urlsplit

from django.core.files.storage import default_storage
from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import runner
from apps.core.models import Task
from apps.videos.models import Video
from apps.videos.serializers import VideoSerializer

MAX_URL_LENGTH = 2000


def validate_fetch_url(url):
    """Return a normalized URL string, or an error message when invalid.

    Accepts only absolute http(s) URLs of at most ``MAX_URL_LENGTH``
    characters; the scheme comparison is case-insensitive per RFC 3986.
    """
    if not isinstance(url, str):
        return None, "url is required"
    url = url.strip()
    if not url:
        return None, "url is required"
    if len(url) > MAX_URL_LENGTH:
        return None, f"url must be at most {MAX_URL_LENGTH} characters"
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None, "url must be an absolute http:// or https:// URL"
    return url, None


class FetchView(APIView):
    """POST /api/fetch/ — download a video asynchronously."""

    def post(self, request):
        """Validate the payload and submit a FETCH task.

        Returns 202 {"task_id": ...} on success, 400 on validation errors
        and 503 while the downloader module is unavailable.
        """
        url, error = validate_fetch_url(request.data.get("url"))
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        # Imported lazily: the downloader workstream may still be writing
        # this module; the rest of the API must keep working without it.
        try:
            from apps.videos.services.fetch import run_fetch
        except ImportError:
            return Response(
                {"error": "fetch service unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        task = runner.submit(Task.TYPE_FETCH, run_fetch, url)
        return Response({"task_id": str(task.id)}, status=status.HTTP_202_ACCEPTED)


class VideoListView(APIView):
    """GET /api/videos/ — list downloaded videos."""

    def get(self, request):
        """Return every Video, newest first (model Meta ordering)."""
        serializer = VideoSerializer(Video.objects.all(), many=True)
        return Response({"videos": serializer.data}, status=status.HTTP_200_OK)


class VideoDetailView(APIView):
    """GET/DELETE /api/videos/<id>/ — inspect or remove a video."""

    def get(self, request, video_id):
        """Return a single Video, or 404 for an unknown id."""
        video = get_object_or_404(Video, pk=video_id)
        serializer = VideoSerializer(video)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, video_id):
        """Delete the Video row and its files from storage.

        Missing files on disk are tolerated (guarding against manual
        cleanup) so the row is always removed. Returns 204.
        """
        video = get_object_or_404(Video, pk=video_id)
        for field_file in (video.file, video.thumbnail):
            if field_file and field_file.name:
                try:
                    default_storage.delete(field_file.name)
                except (FileNotFoundError, OSError, ValueError):
                    # File already gone or storage misconfigured — the row
                    # must still be deleted.
                    pass
        video.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
