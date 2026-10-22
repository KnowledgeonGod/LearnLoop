from django.urls import path

from . import views

urlpatterns = [
    path("topics/<int:topic_id>/plan/", views.plan, name="plan"),
    path("topics/<int:topic_id>/plan/generate/", views.generate, name="plan_generate"),
    path(
        "topics/<int:topic_id>/plan/jobs/<int:job_id>/",
        views.plan_job_progress,
        name="plan_job_progress",
    ),
    path(
        "topics/<int:topic_id>/plan/jobs/<int:job_id>/status/",
        views.plan_job_status,
        name="plan_job_status",
    ),
]