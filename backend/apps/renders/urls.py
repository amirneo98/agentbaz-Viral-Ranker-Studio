from django.urls import path

from apps.renders import views
from apps.renders import views_openreel
from apps.renders import views_urls

urlpatterns = [
    path("render/urls/", views_urls.UrlRenderView.as_view(),
         name="render-urls"),
    path("render/urls/preview/", views_urls.UrlPreviewView.as_view(),
         name="render-urls-preview"),
    path("render/openreel/", views_openreel.OpenReelRenderView.as_view(),
         name="render-openreel"),
    path("proxy/", views_openreel.ProxyDownloadView.as_view(),
         name="proxy-download"),
    path("render/", views.RenderView.as_view(), name="render"),
]
