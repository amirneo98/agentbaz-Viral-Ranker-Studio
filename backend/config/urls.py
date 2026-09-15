"""Root URL configuration."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("apps.core.urls")),
    path("api/", include("apps.videos.urls")),
    path("api/", include("apps.renders.urls")),
    path("api/", include("apps.bgm.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Local-only stack: serve media from Django too, so the backend port (8000)
# works standalone — nginx (frontend, port 3000) remains the primary path via
# its /media/ alias on the shared volume. django.conf.urls.static.static()
# is a DEBUG-only helper, hence the explicit re_path here.
urlpatterns += [
    re_path(
        r"^media/(?P<path>.*)$",
        serve,
        {"document_root": settings.MEDIA_ROOT},
    ),
]
