import logging
from apps.feedback.selectors import feedback_for
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from apps.resources.selectors import resources_by_step
from apps.resources.services import attach_resources
from apps.topics.models import Topic

from .selectors import get_plan_page
from .services import PlanGenerationFailed, QuizRequired, generate_plan

logger = logging.getLogger(__name__)

GENERIC_ERROR = "We couldn't build your plan right now. Please try again in a moment."


def _plan_context(user, topic, **extra):
    page = get_plan_page(user, topic)  # always this user's own plan
    rows = []
    if page.plan:
        by_step = resources_by_step(page.plan)
        mine = feedback_for(user, [r for found in by_step.values() for r in found])  # one query
        rows = [
            (step, [(resource, mine.get(resource.pk)) for resource in by_step.get(step.pk, [])])
            for step in page.plan.steps.all()
        ]
    return {
        "topic": topic,
        "page": page,
        "rows": rows,
        "missing_resources": any(not found for _, found in rows),
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
    except PlanGenerationFailed:
        # Log the real reason for us, show a generic message to the user.
        logger.exception("Plan generation failed (user=%s, topic=%s)", request.user.pk, topic.pk)
        context = _plan_context(request.user, topic, error=GENERIC_ERROR)
        return render(request, "learning/plan.html", context, status=503)

    try:
        attach_resources(plan=learning_plan)
    except Exception:  # best effort: a plan without links is still useful
        logger.exception("Attaching resources failed (plan=%s)", learning_plan.pk)
    return redirect("plan", topic_id=topic.pk)