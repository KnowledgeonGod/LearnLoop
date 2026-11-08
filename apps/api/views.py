import logging

from django.conf import settings
from django.contrib.auth import authenticate
from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.services import EmailAlreadyRegistered, register_user, set_preferred_format
from apps.feedback.services import InvalidFeedback, set_feedback
from apps.learning.jobs import GENERIC_ERROR, enqueue_plan_job, worker_may_be_stopped
from apps.learning.models import PlanJob
from apps.learning.personalize import ranker_for
from apps.learning.services import (
    PlanConfigurationFailed,
    PlanGenerationFailed,
    PlanQuotaExceeded,
    QuizRequired,
    generate_plan,
)
from apps.learning.views import _plan_context  # same filtering as the website's plan page
from apps.quiz.models import ANSWER_FIELDS, Depth, Goal, Level
from apps.quiz.selectors import get_attempt, initial_answers
from apps.quiz.services import InvalidQuiz, submit_quiz
from apps.resources.models import Resource
from apps.resources.services import attach_resources
from apps.topics.models import Topic
from apps.topics.selectors import popular_topics, recent_topics_for
from apps.topics.services import InvalidTopic, record_search

from .serializers import (
    AuthResponseSerializer,
    DashboardSerializer,
    ErrorSerializer,
    FeedbackActionSerializer,
    FeedbackResultSerializer,
    LoginSerializer,
    PlanJobSerializer,
    PlanSerializer,
    ProfileUpdateSerializer,
    QuizAnswersSerializer,
    QuizStateSerializer,
    SignupSerializer,
    TopicDetailSerializer,
    TopicSearchSerializer,
    TopicSerializer,
    UserSerializer,
)

logger = logging.getLogger(__name__)

VALIDATION_ERROR = OpenApiResponse(description='Validation errors, e.g. {"email": ["Enter a valid email address."]}')
QUIZ_REQUIRED_MESSAGE = "Take the quiz first, then create your plan."


def _error(detail, status, code=None):
    body = {"detail": detail}
    if code:
        body["code"] = code
    return Response(body, status=status)


def _job_payload(job):
    return {
        "id": job.pk,
        "status": job.status,
        "status_display": job.get_status_display(),
        "error": job.error,
        "plan_ready": job.status == PlanJob.Status.SUCCEEDED,
        "worker_may_be_stopped": worker_may_be_stopped(job),
    }


def _feedback_payload(row):
    return {"rating": row.rating, "progress": row.progress} if row else None


def _plan_payload(user, topic):
    ctx = _plan_context(user, topic)
    page = ctx["page"]
    steps = [
        {
            "id": step.pk,
            "position": step.position,
            "title": step.title,
            "explanation": step.explanation,
            "minutes": step.minutes,
            "step_type": step.step_type,
            "resources": [
                {
                    "id": resource.pk,
                    "title": resource.title,
                    "url": resource.url,
                    "kind": resource.kind,
                    "source": resource.source,
                    "why_this_one": reasons,
                    "feedback": _feedback_payload(row),
                }
                for resource, row, reasons in items
            ],
        }
        for step, items in ctx["rows"]
    ]
    hidden = [
        {
            "id": row.resource.pk,
            "title": row.resource.title,
            "url": row.resource.url,
            "kind": row.resource.kind,
            "feedback": _feedback_payload(row),
        }
        for row in ctx["hidden"]
    ]
    job = ctx["active_job"]
    return {
        "state": page.state,
        "generated_at": page.plan.generated_at if page.plan else None,
        "source": page.plan.source if page.plan else None,
        "steps": steps,
        "hidden_resources": hidden,
        "quota_remaining": ctx["quota_remaining"],
        "active_job": _job_payload(job) if job else None,
    }


def _quiz_state(user, topic):
    attempt = get_attempt(user, topic)
    if attempt:
        data = {field: getattr(attempt, field) for field in ANSWER_FIELDS}
        data["completed"] = True
        return data
    return {
        "level": Level.BEGINNER.value,
        "goal": Goal.CURIOSITY.value,
        "hours_per_week": 5,
        "weeks_available": 4,
        "depth": Depth.OVERVIEW.value,
        **initial_answers(user, topic),  # the user's default format
        "completed": False,
    }


# ---------- auth and profile ----------

class SignupView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "signup"

    @extend_schema(
        tags=["auth"],
        summary="Create an account",
        description="Creates an account and returns a token. Send the token on every later request "
        "as the header `Authorization: Token <token>`.",
        request=SignupSerializer,
        responses={201: AuthResponseSerializer, 400: VALIDATION_ERROR},
    )
    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            user = register_user(name=data["name"], email=data["email"], password=data["password"])
        except EmailAlreadyRegistered:
            raise serializers.ValidationError({"email": ["An account with this email already exists."]})
        token, _ = Token.objects.get_or_create(user=user)
        return Response(AuthResponseSerializer({"token": token.key, "user": user}).data, status=201)


class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "login"

    @extend_schema(
        tags=["auth"],
        summary="Log in",
        description="Returns a token. Five failed attempts lock the account for one hour.",
        request=LoginSerializer,
        responses={200: AuthResponseSerializer, 400: VALIDATION_ERROR, 401: ErrorSerializer},
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = authenticate(
            request=request._request,  # django-axes needs the plain Django request
            username=data["email"].strip().lower(),
            password=data["password"],
        )
        if user is None:
            return _error("Invalid email or password.", 401)
        token, _ = Token.objects.get_or_create(user=user)
        return Response(AuthResponseSerializer({"token": token.key, "user": user}).data)


class LogoutView(APIView):
    @extend_schema(
        tags=["auth"],
        summary="Log out",
        description="Deletes the user's token. Log in again to get a new one.",
        request=None,
        responses={204: OpenApiResponse(description="Logged out")},
    )
    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=204)


class ProfileView(APIView):
    @extend_schema(tags=["profile"], summary="Get my profile", responses=UserSerializer)
    def get(self, request):
        return Response(UserSerializer(request.user).data)

    @extend_schema(
        tags=["profile"],
        summary="Change my preferred learning format",
        request=ProfileUpdateSerializer,
        responses={200: UserSerializer, 400: VALIDATION_ERROR},
    )
    def patch(self, request):
        serializer = ProfileUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        set_preferred_format(user=request.user, value=serializer.validated_data["preferred_format"])
        return Response(UserSerializer(request.user).data)


# ---------- topics ----------

class DashboardView(APIView):
    @extend_schema(
        tags=["topics"],
        summary="Dashboard",
        description="The user's most recent searches, and topics that at least two different users have searched.",
        responses=DashboardSerializer,
    )
    def get(self, request):
        payload = {"recent": recent_topics_for(request.user), "popular": popular_topics()}
        return Response(DashboardSerializer(payload).data)


class TopicSearchView(APIView):
    throttle_scope = "search"

    @extend_schema(
        tags=["topics"],
        summary="Search a topic",
        description="Finds or creates the topic and adds it to the user's history. "
        "Use the returned `id` for the quiz and plan endpoints.",
        request=TopicSearchSerializer,
        responses={200: TopicSerializer, 400: VALIDATION_ERROR},
    )
    def post(self, request):
        serializer = TopicSearchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            topic = record_search(user=request.user, raw_name=serializer.validated_data["query"])
        except InvalidTopic as exc:
            raise serializers.ValidationError({"query": [str(exc)]})
        return Response(TopicSerializer(topic).data)


class TopicDetailView(APIView):
    @extend_schema(
        tags=["topics"],
        summary="Get a topic",
        description="`plan_state` tells the app what to show next: take the quiz, generate a plan, or show the plan.",
        responses={200: TopicDetailSerializer, 404: ErrorSerializer},
    )
    def get(self, request, topic_id):
        topic = get_object_or_404(Topic, pk=topic_id)
        return Response(TopicDetailSerializer(topic, context={"request": request}).data)


# ---------- quiz ----------

class QuizView(APIView):
    throttle_scope = "quiz"

    @extend_schema(
        tags=["quiz"],
        summary="Get my quiz answers for a topic",
        description="Returns the saved answers, or defaults with `completed: false` if the quiz was not taken yet.",
        responses={200: QuizStateSerializer, 404: ErrorSerializer},
    )
    def get(self, request, topic_id):
        topic = get_object_or_404(Topic, pk=topic_id)
        return Response(QuizStateSerializer(_quiz_state(request.user, topic)).data)

    @extend_schema(
        tags=["quiz"],
        summary="Submit my quiz answers for a topic",
        description="Send all six answers. Submitting again replaces the earlier answers; "
        "an existing plan then becomes `stale` until it is regenerated.",
        request=QuizAnswersSerializer,
        responses={200: QuizStateSerializer, 400: VALIDATION_ERROR, 404: ErrorSerializer},
    )
    def put(self, request, topic_id):
        topic = get_object_or_404(Topic, pk=topic_id)
        serializer = QuizAnswersSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            submit_quiz(user=request.user, topic=topic, answers=dict(serializer.validated_data))
        except InvalidQuiz as exc:
            raise serializers.ValidationError(exc.errors)
        return Response(QuizStateSerializer(_quiz_state(request.user, topic)).data)


# ---------- learning plan ----------

class PlanView(APIView):
    @extend_schema(
        tags=["plan"],
        summary="Get my learning plan for a topic",
        description="Steps with their resources, in order. Resources the user disliked or skipped are "
        "listed separately in `hidden_resources` so the action can be undone.",
        responses={200: PlanSerializer, 404: ErrorSerializer},
    )
    def get(self, request, topic_id):
        topic = get_object_or_404(Topic, pk=topic_id)
        return Response(PlanSerializer(_plan_payload(request.user, topic)).data)


class PlanGenerateView(APIView):
    throttle_scope = "plan_generate"

    @extend_schema(
        tags=["plan"],
        summary="Generate or rebuild my learning plan",
        description="The quiz must be submitted first. If background jobs are on, this returns **202** "
        "with a job: poll the job endpoint until `plan_ready` is true, then fetch the plan. "
        "Otherwise the plan is built during the request (it can take a while) and returned with **201**.",
        request=None,
        responses={
            201: PlanSerializer,
            202: PlanJobSerializer,
            404: ErrorSerializer,
            409: OpenApiResponse(response=ErrorSerializer, description="Quiz not taken yet (code: quiz_required)"),
            429: OpenApiResponse(response=ErrorSerializer, description="Daily plan limit reached"),
            503: OpenApiResponse(response=ErrorSerializer, description="Plan backend failed or is not configured"),
        },
    )
    def post(self, request, topic_id):
        topic = get_object_or_404(Topic, pk=topic_id)
        user = request.user

        if getattr(settings, "PLAN_JOBS_ENABLED", False):
            try:
                job = enqueue_plan_job(user=user, topic=topic)
            except QuizRequired:
                return _error(QUIZ_REQUIRED_MESSAGE, 409, "quiz_required")
            except PlanQuotaExceeded as exc:
                return _error(str(exc), 429, "quota_exceeded")
            except Exception:
                logger.exception("Could not enqueue plan job (user=%s, topic=%s)", user.pk, topic.pk)
                return _error(GENERIC_ERROR, 503)
            return Response(PlanJobSerializer(_job_payload(job)).data, status=202)

        try:
            learning_plan = generate_plan(user=user, topic=topic)
        except QuizRequired:
            return _error(QUIZ_REQUIRED_MESSAGE, 409, "quiz_required")
        except PlanQuotaExceeded as exc:
            return _error(str(exc), 429, "quota_exceeded")
        except PlanConfigurationFailed as exc:
            logger.exception("Plan backend is not configured (user=%s, topic=%s)", user.pk, topic.pk)
            return _error(str(exc), 503)
        except PlanGenerationFailed:
            logger.exception("Plan generation failed (user=%s, topic=%s)", user.pk, topic.pk)
            return _error(GENERIC_ERROR, 503)

        try:
            attach_resources(plan=learning_plan, rank=ranker_for(user, topic))
        except Exception:  # best effort: a plan without links is still useful
            logger.exception("Attaching resources failed (plan=%s)", learning_plan.pk)
        return Response(PlanSerializer(_plan_payload(user, topic)).data, status=201)


class PlanJobView(APIView):
    @extend_schema(
        tags=["plan"],
        summary="Check a plan generation job",
        description="Poll every couple of seconds. Stop when `status` is `succeeded` or `failed`.",
        responses={200: PlanJobSerializer, 404: ErrorSerializer},
    )
    def get(self, request, topic_id, job_id):
        topic = get_object_or_404(Topic, pk=topic_id)
        job = get_object_or_404(PlanJob, pk=job_id, user=request.user, topic=topic)
        return Response(PlanJobSerializer(_job_payload(job)).data)


# ---------- feedback ----------

class ResourceFeedbackView(APIView):
    throttle_scope = "feedback"

    @extend_schema(
        tags=["feedback"],
        summary="Give feedback on a resource",
        description="Actions: `like`, `dislike`, `clear_rating`, `done`, `skip`, `clear_progress`. "
        "Feedback is private to the user and changes what their next plan rebuild shows.",
        request=FeedbackActionSerializer,
        responses={200: FeedbackResultSerializer, 400: VALIDATION_ERROR, 404: ErrorSerializer},
    )
    def post(self, request, resource_id):
        serializer = FeedbackActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Only resources from this user's own plans, or ones they already gave feedback on.
        resource = get_object_or_404(
            Resource.objects.filter(
                Q(step_links__step__plan__user=request.user) | Q(feedback__user=request.user)
            ).distinct(),
            pk=resource_id,
        )
        try:
            row = set_feedback(user=request.user, resource=resource, action=serializer.validated_data["action"])
        except InvalidFeedback:
            raise serializers.ValidationError({"action": ["Unknown action."]})
        payload = {
            "resource_id": resource.pk,
            "topic_id": resource.topic_id,
            "rating": row.rating if row else None,
            "progress": row.progress if row else None,
        }
        return Response(FeedbackResultSerializer(payload).data)