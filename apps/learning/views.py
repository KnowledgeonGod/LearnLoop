import logging

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from apps.feedback.selectors import feedback_for, hidden_feedback_for
from apps.resources.selectors import resources_by_step
from apps.resources.services import attach_resources
from apps.topics.models import Topic

from .personalize import ranker_for, reasons_for
from .selectors import get_plan_page
from .services import (
    PlanConfigurationFailed,
    PlanGenerationFailed,
    QuizRequired,
    generate_plan,
)

logger = logging.getLogger(__name__)

GENERIC_ERROR = "We couldn't build your plan right now. Please try again in a moment."


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
    try:
        learning_plan = generate_plan(user=request.user, topic=topic)
    except QuizRequired:
        return redirect("quiz", topic_id=topic.pk)
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