from django.urls import path

from apps.renders import views

urlpatterns = [
    path("render/", views.RenderView.as_view(), name="render"),
]
