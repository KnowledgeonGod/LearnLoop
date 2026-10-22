import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django_ratelimit.decorators import ratelimit

from apps.feedback.selectors import feedback_for, hidden_feedback_for
from apps.resources.selectors import resources_by_step
from apps.resources.services import attach_resources
from apps.topics.models import Topic

from .jobs import GENERIC_ERROR, enqueue_plan_job, worker_may_be_stopped
from .models import PlanJob
from .personalize import ranker_for, reasons_for
from .selectors import get_plan_page
from .services import (
    PlanConfigurationFailed,
    PlanGenerationFailed,
    PlanQuotaExceeded,
    QuizRequired,
    generate_plan,
    plan_usage_remaining,
)

logger = logging.getLogger(__name__)


def _plan_context(user, topic, **extra):
    page = get_plan_page(user, topic)  # always this user's own plan
    rows = []
    hidden_section = False
    hidden = []
    if page.plan:
        hidden = list(hidden_feedback_for(user, topic))  # one query
        hidden_ids = {row.resource_id for row in hidden}
        by_step = resources_by_step(page.plan)
        # Hidden resources are filtered out immediately, even before a rebuild, so the
        # plan and the "hidden" list can never show the same resource twice.
        visible = {
            step_id: [resource for resource in found if resource.pk not in hidden_ids]
            for step_id, found in by_step.items()
        }
        shown = [resource for found in visible.values() for resource in found]
        mine = feedback_for(user, shown)  # one query
        why = reasons_for(user, topic, shown)  # two queries
        # A step with no resources is not useful. When every resource of a type has
        # been hidden by feedback (or could not be fetched), its whole section goes.
        rows = [
            (
                step,
                [
                    (resource, mine.get(resource.pk), why.get(resource.pk, []))
                    for resource in visible.get(step.pk, [])
                ],
            )
            for step in page.plan.steps.all()
            if visible.get(step.pk)
        ]
        hidden_section = bool(page.plan.steps.exists()) and not rows
    return {
        "topic": topic,
        "page": page,
        "rows": rows,
        "hidden_section": hidden_section,
        "hidden": hidden,
        "quota_remaining": plan_usage_remaining(user),
        "active_job": PlanJob.objects.filter(
            user=user,
            topic=topic,
            status__in=(PlanJob.Status.QUEUED, PlanJob.Status.RUNNING),
        )
        .order_by("-created_at")
        .first(),
        **extra,
    }


@login_required
def plan(request, topic_id):
    topic = get_object_or_404(Topic, pk=topic_id)  # topics are shared, so an ID is fine
    return render(request, "learning/plan.html", _plan_context(request.user, topic))


@login_required
@ratelimit(key="user", rate="10/h", method="POST", block=True)  # real LLM calls cost money
@require_POST
def generate(request, topic_id):
    topic = get_object_or_404(Topic, pk=topic_id)

    if getattr(settings, "PLAN_JOBS_ENABLED", False):
        try:
            job = enqueue_plan_job(user=request.user, topic=topic)
        except QuizRequired:
            return redirect("quiz", topic_id=topic.pk)
        except PlanQuotaExceeded as exc:
            context = _plan_context(request.user, topic, error=str(exc))
            return render(request, "learning/plan.html", context, status=429)
        except Exception:
            logger.exception("Could not enqueue plan job (user=%s, topic=%s)", request.user.pk, topic.pk)
            context = _plan_context(request.user, topic, error=GENERIC_ERROR)
            return render(request, "learning/plan.html", context, status=503)
        return redirect("plan_job_progress", topic_id=topic.pk, job_id=job.pk)

    try:
        learning_plan = generate_plan(user=request.user, topic=topic)
    except QuizRequired:
        return redirect("quiz", topic_id=topic.pk)
    except PlanQuotaExceeded as exc:
        logger.warning("Plan quota exceeded (user=%s, topic=%s)", request.user.pk, topic.pk)
        context = _plan_context(request.user, topic, error=str(exc))
        return render(request, "learning/plan.html", context, status=429)
    except PlanConfigurationFailed as exc:
        # Configuration messages are written by us and never contain a key, so the
        # user can act on them instead of seeing a dead end.
        logger.exception("Plan backend is not configured (user=%s, topic=%s)", request.user.pk, topic.pk)
        context = _plan_context(request.user, topic, error=str(exc))
        return render(request, "learning/plan.html", context, status=503)
    except PlanGenerationFailed:
        # Log the real reason for us, show a generic message to the user.
        logger.exception("Plan generation failed (user=%s, topic=%s)", request.user.pk, topic.pk)
        context = _plan_context(request.user, topic, error=GENERIC_ERROR)
        return render(request, "learning/plan.html", context, status=503)

    try:
        attach_resources(plan=learning_plan, rank=ranker_for(request.user, topic))
    except Exception:  # best effort: a plan without links is still useful
        logger.exception("Attaching resources failed (plan=%s)", learning_plan.pk)
    return redirect("plan", topic_id=topic.pk)


@login_required
def plan_job_progress(request, topic_id, job_id):
    topic = get_object_or_404(Topic, pk=topic_id)
    job = get_object_or_404(PlanJob, pk=job_id, user=request.user, topic=topic)
    if job.status == PlanJob.Status.SUCCEEDED:
        return redirect("plan", topic_id=topic.pk)
    return render(
        request,
        "learning/plan_job.html",
        {
            "topic": topic,
            "job": job,
            "worker_may_be_stopped": worker_may_be_stopped(job),
            "status_url": reverse("plan_job_status", args=[topic.pk, job.pk]),
            "plan_url": reverse("plan", args=[topic.pk]),
        },
    )


@login_required
@require_GET
def plan_job_status(request, topic_id, job_id):
    topic = get_object_or_404(Topic, pk=topic_id)
    job = get_object_or_404(PlanJob, pk=job_id, user=request.user, topic=topic)
    response = JsonResponse(
        {
            "status": job.status,
            "status_display": job.get_status_display(),
            "error": job.error,
            "redirect_url": (
                reverse("plan", args=[topic.pk])
                if job.status == PlanJob.Status.SUCCEEDED
                else None
            ),
            "worker_may_be_stopped": worker_may_be_stopped(job),
        }
    )
    response["Cache-Control"] = "no-store"
    return response