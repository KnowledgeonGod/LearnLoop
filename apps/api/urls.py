from django.urls import path

from . import views

urlpatterns = [
    path("auth/signup/", views.SignupView.as_view(), name="api_signup"),
    path("auth/login/", views.LoginView.as_view(), name="api_login"),
    path("auth/logout/", views.LogoutView.as_view(), name="api_logout"),
    path("profile/", views.ProfileView.as_view(), name="api_profile"),
    path("dashboard/", views.DashboardView.as_view(), name="api_dashboard"),
    path("topics/search/", views.TopicSearchView.as_view(), name="api_topic_search"),
    path("topics/<int:topic_id>/", views.TopicDetailView.as_view(), name="api_topic_detail"),
    path("topics/<int:topic_id>/quiz/", views.QuizView.as_view(), name="api_quiz"),
    path("topics/<int:topic_id>/plan/", views.PlanView.as_view(), name="api_plan"),
    path("topics/<int:topic_id>/plan/generate/", views.PlanGenerateView.as_view(), name="api_plan_generate"),
    path(
        "topics/<int:topic_id>/plan/jobs/<int:job_id>/",
        views.PlanJobView.as_view(),
        name="api_plan_job",
    ),
    path("resources/<int:resource_id>/feedback/", views.ResourceFeedbackView.as_view(), name="api_resource_feedback"),
]