from django.urls import path

from apps.core import views

urlpatterns = [
    path("tasks/<uuid:task_id>/", views.TaskDetailView.as_view(), name="task-detail"),
    path("health/", views.HealthView.as_view(), name="health"),
]
