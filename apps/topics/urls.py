from django.urls import path

from . import views

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("topics/search/", views.search, name="topic_search"),
    path("topics/<int:topic_id>/", views.topic_detail, name="topic_detail"),
]