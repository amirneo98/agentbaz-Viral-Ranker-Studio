"""BGM library API views."""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.bgm.models import BgmTrack
from apps.bgm.serializers import BgmTrackSerializer


class BgmListView(APIView):
    """GET /api/bgm/ — list available background music tracks."""

    def get(self, request):
        """Return every BgmTrack (alphabetical, model Meta ordering)."""
        serializer = BgmTrackSerializer(BgmTrack.objects.all(), many=True)
        return Response({"bgm": serializer.data}, status=status.HTTP_200_OK)
