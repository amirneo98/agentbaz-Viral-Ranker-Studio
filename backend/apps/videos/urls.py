from django.urls import path

from apps.videos import views

urlpatterns = [
    path("fetch/", views.FetchView.as_view(), name="fetch"),
    path("videos/", views.VideoListView.as_view(), name="video-list"),
    path(
        "videos/<int:video_id>/",
        views.VideoDetailView.as_view(),
        name="video-detail",
    ),
]
