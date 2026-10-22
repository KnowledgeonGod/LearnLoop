from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.quiz.selectors import get_attempt
from services.llm import LLMConfigurationError, LLMError, PlanRequest, get_llm_client

from .models import (
    MAX_EXPLANATION_LENGTH,
    MAX_MINUTES,
    MAX_STEPS,
    MAX_TITLE_LENGTH,
    MIN_MINUTES,
    MIN_STEPS,
    LearningPlan,
    PlanStep,
    PlanUsage,
    StepType,
)
from .usage import record_llm_usage


class QuizRequired(Exception):
    pass


class PlanGenerationFailed(Exception):
    pass


class PlanConfigurationFailed(PlanGenerationFailed):
    """The selected backend is missing configuration. Its message is safe to show."""


class PlanQuotaExceeded(PlanGenerationFailed):
    """The user has used today's plan-build allowance. Its message is safe to show."""


class InvalidPlan(PlanGenerationFailed):
    pass


def _plan_daily_limit() -> int:
    try:
        return max(int(getattr(settings, "PLAN_DAILY_LIMIT", 0)), 0)
    except (TypeError, ValueError):
        return 0


def _plan_usage_today(user) -> int:
    return (
        PlanUsage.objects.filter(user=user, day=timezone.localdate())
        .values_list("count", flat=True)
        .first()
        or 0
    )


def plan_usage_remaining(user):
    """How many builds are left today, or None when the limit is disabled."""
    limit = _plan_daily_limit()
    if limit <= 0:
        return None
    return max(limit - _plan_usage_today(user), 0)


def ensure_plan_quota_available(user) -> None:
    """Check without consuming, so a queued job can fail fast at enqueue time."""
    limit = _plan_daily_limit()
    if limit <= 0:
        return
    if _plan_usage_today(user) >= limit:
        raise PlanQuotaExceeded(
            f"You have reached today's limit of {limit} plan builds. Try again tomorrow."
        )


def consume_plan_quota(user) -> None:
    """Consume one build. The row is locked so concurrent requests cannot overspend."""
    limit = _plan_daily_limit()
    if limit <= 0:
        return
    with transaction.atomic():
        usage, _ = PlanUsage.objects.select_for_update().get_or_create(
            user=user, day=timezone.localdate(), defaults={"count": 0}
        )
        if usage.count >= limit:
            raise PlanQuotaExceeded(
                f"You have reached today's limit of {limit} plan builds. Try again tomorrow."
            )
        usage.count += 1
        usage.save(update_fields=["count"])


def validate_drafts(drafts) -> list:
    """LLM output is untrusted input. Check every field before it touches the database."""
    try:
        drafts = list(drafts)
    except TypeError:
        raise InvalidPlan("The plan was not a list of steps.") from None
    if not (MIN_STEPS <= len(drafts) <= MAX_STEPS):
        raise InvalidPlan(f"A plan needs {MIN_STEPS} to {MAX_STEPS} steps, got {len(drafts)}.")

    cleaned = []
    for draft in drafts:
        title = getattr(draft, "title", None)
        explanation = getattr(draft, "explanation", None)
        minutes = getattr(draft, "minutes", None)
        step_type = getattr(draft, "step_type", None)

        if not isinstance(title, str) or not isinstance(explanation, str):
            raise InvalidPlan("Step text must be a string.")
        title = " ".join(title.split())
        explanation = explanation.strip()
        if not title or len(title) > MAX_TITLE_LENGTH:
            raise InvalidPlan("Step title is empty or too long.")
        if not explanation or len(explanation) > MAX_EXPLANATION_LENGTH:
            raise InvalidPlan("Step explanation is empty or too long.")
        if step_type not in StepType.values:
            raise InvalidPlan(f"Unknown step type: {step_type!r}.")
        if (
            isinstance(minutes, bool)
            or not isinstance(minutes, int)
            or not (MIN_MINUTES <= minutes <= MAX_MINUTES)
        ):
            raise InvalidPlan("Step minutes are missing or out of range.")
        cleaned.append((title, explanation, minutes, step_type))
    return cleaned


def generate_plan(*, user, topic, client=None) -> LearningPlan:
    attempt = get_attempt(user, topic)  # always this user's own answers
    if attempt is None:
        raise QuizRequired()

    # Check before the slow call so an exhausted user does not spend provider tokens.
    # The allowance is consumed only after a usable plan has been validated.
    ensure_plan_quota_available(user)

    request = PlanRequest(
        topic=topic.name,
        level=attempt.level,
        goal=attempt.goal,
        hours_per_week=attempt.hours_per_week,
        weeks_available=attempt.weeks_available,
        preferred_format=attempt.preferred_format,
        depth=attempt.depth,
    )

    # The slow, untrusted part runs OUTSIDE the transaction, so no database lock
    # is held while we wait on a network call.
    try:
        client = client or get_llm_client()
        try:
            drafts = client.generate_plan(request)
        finally:
            # Record provider usage even when the call raised later in parsing.
            record_llm_usage(client)
    except LLMConfigurationError as exc:
        raise PlanConfigurationFailed(str(exc)) from exc
    except LLMError as exc:
        raise PlanGenerationFailed(str(exc)) from exc
    steps = validate_drafts(drafts)
    consume_plan_quota(user)

    with transaction.atomic():  # plan and steps: all or nothing
        # update_or_create locks the plan row, so two simultaneous rebuilds queue up.
        plan, _ = LearningPlan.objects.update_or_create(
            user=user,
            topic=topic,
            defaults={
                "based_on_quiz_at": attempt.updated_at,
                "generated_at": timezone.now(),
                "source": getattr(client, "name", "unknown"),
            },
        )
        plan.steps.all().delete()  # if the next line fails, this delete rolls back too
        PlanStep.objects.bulk_create(
            PlanStep(
                plan=plan, position=position, title=title,
                explanation=explanation, minutes=minutes, step_type=step_type,
            )
            for position, (title, explanation, minutes, step_type) in enumerate(steps, start=1)
        )
    return plan