from django.urls import path

from apps.bgm import views

urlpatterns = [
    path("bgm/", views.BgmListView.as_view(), name="bgm-list"),
]
