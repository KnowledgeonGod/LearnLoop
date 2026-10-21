from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.accounts.urls")),
    path("", include("apps.topics.urls")),
    path("", include("apps.quiz.urls")),
    path("", include("apps.learning.urls")),
    path("", include("apps.feedback.urls")),
]