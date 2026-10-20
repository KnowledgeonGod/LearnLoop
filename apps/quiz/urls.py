from django.urls import path

from . import views

urlpatterns = [
    path("topics/<int:topic_id>/quiz/", views.quiz, name="quiz"),
]