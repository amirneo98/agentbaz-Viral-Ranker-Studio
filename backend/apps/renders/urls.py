from django.urls import path

from apps.renders import views
from apps.renders import views_openreel

urlpatterns = [
    path("render/", views.RenderView.as_view(), name="render"),
    path("render/openreel/", views_openreel.OpenReelRenderView.as_view(),
         name="render-openreel"),
    path("proxy/", views_openreel.ProxyDownloadView.as_view(),
         name="proxy-download"),
]
