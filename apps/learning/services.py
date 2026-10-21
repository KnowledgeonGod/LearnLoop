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
    StepType,
)


class QuizRequired(Exception):
    pass


class PlanGenerationFailed(Exception):
    pass


class PlanConfigurationFailed(PlanGenerationFailed):
    """The selected backend is missing configuration. Its message is safe to show."""


class InvalidPlan(PlanGenerationFailed):
    pass


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
        drafts = client.generate_plan(request)
    except LLMConfigurationError as exc:
        raise PlanConfigurationFailed(str(exc)) from exc
    except LLMError as exc:
        raise PlanGenerationFailed(str(exc)) from exc
    steps = validate_drafts(drafts)

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