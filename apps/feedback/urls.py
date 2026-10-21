from django.urls import path

from . import views

urlpatterns = [
    path("resources/<int:resource_id>/feedback/", views.feedback, name="resource_feedback"),
]