from dataclasses import dataclass

from apps.quiz.selectors import get_attempt

from .models import LearningPlan

NEEDS_QUIZ, NO_PLAN, STALE, READY = "needs_quiz", "no_plan", "stale", "ready"


@dataclass(frozen=True)
class PlanPage:
    state: str
    plan: LearningPlan | None


def get_plan_page(user, topic) -> PlanPage:
    attempt = get_attempt(user, topic)
    # prefetch_related loads all steps in one extra query (no N+1);
    # filtering by user means nobody ever sees another user's plan.
    plan = (
        LearningPlan.objects.filter(user=user, topic=topic)
        .prefetch_related("steps")
        .first()
    )
    if plan is None:
        return PlanPage(NO_PLAN if attempt else NEEDS_QUIZ, None)
    if attempt and attempt.updated_at > plan.based_on_quiz_at:
        return PlanPage(STALE, plan)
    return PlanPage(READY, plan)