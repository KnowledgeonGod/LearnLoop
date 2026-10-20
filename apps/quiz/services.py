from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.services import set_preferred_format

from .models import ANSWER_FIELDS, QuizAttempt


class InvalidQuiz(ValueError):
    def __init__(self, errors):
        super().__init__(str(errors))
        self.errors = errors


def validate_answers(answers: dict) -> dict:
    """Validates with the model's own rules (choices and ranges), so the form,
    this service and the database can't drift apart. Returns cleaned values."""
    unknown = set(answers) - set(ANSWER_FIELDS)
    if unknown:
        raise InvalidQuiz({"__all__": [f"Unknown answers: {sorted(unknown)}"]})

    # Missing answers are passed as None explicitly, so model defaults can't hide them.
    candidate = QuizAttempt(**{f: answers.get(f) for f in ANSWER_FIELDS})
    try:
        candidate.full_clean(
            exclude=["user", "topic", "updated_at"],
            validate_unique=False,
            validate_constraints=False,
        )
    except ValidationError as exc:
        raise InvalidQuiz(exc.message_dict) from exc
    return {f: getattr(candidate, f) for f in ANSWER_FIELDS}


def submit_quiz(*, user, topic, answers: dict) -> QuizAttempt:
    cleaned = validate_answers(answers)
    with transaction.atomic():  # attempt and profile default: all or nothing
        # Idempotent: submitting the same answers twice leaves one row.
        # Retaking updates the row instead of adding another.
        attempt, _ = QuizAttempt.objects.update_or_create(
            user=user,
            topic=topic,
            defaults={**cleaned, "updated_at": timezone.now()},
        )
        # Their latest format choice becomes the default for the next topic.
        set_preferred_format(user=user, value=cleaned["preferred_format"])
    return attempt