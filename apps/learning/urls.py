from django.urls import path

from . import views

urlpatterns = [
    path("topics/<int:topic_id>/plan/", views.plan, name="plan"),
    path("topics/<int:topic_id>/plan/generate/", views.generate, name="plan_generate"),
]