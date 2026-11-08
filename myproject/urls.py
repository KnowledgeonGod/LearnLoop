from django.contrib import admin
from django.urls import include, path
from django.shortcuts import redirect
from myproject.views import healthz
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/", include("apps.api.urls")),
    path("", lambda request: redirect("dashboard"), name="home"),
    path("", include("apps.accounts.urls")),
    path("", include("apps.topics.urls")),
    path("", include("apps.quiz.urls")),
    path("", include("apps.learning.urls")),
    path("", include("apps.feedback.urls")),
]