from django.urls import path

from apps.studio import views

urlpatterns = [
    path("presets/", views.PresetListCreateView.as_view(), name="preset-list"),
    path(
        "presets/<int:preset_id>/",
        views.PresetDetailView.as_view(),
        name="preset-detail",
    ),
    path("clips/", views.ClipListCreateView.as_view(), name="clip-list"),
    path("clips/reorder/", views.ClipReorderView.as_view(), name="clip-reorder"),
    path(
        "clips/<uuid:clip_id>/",
        views.ClipDetailView.as_view(),
        name="clip-detail",
    ),
    path("stream-info/", views.StreamInfoView.as_view(), name="stream-info"),
    path(
        "download-section/",
        views.DownloadSectionView.as_view(),
        name="download-section",
    ),
]
