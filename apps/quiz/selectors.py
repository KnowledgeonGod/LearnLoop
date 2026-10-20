from apps.accounts.models import LearningFormat, Profile

from .models import ANSWER_FIELDS, QuizAttempt


def get_attempt(user, topic):
    # Always filtered by the user passed in, so nobody sees another user's answers.
    return QuizAttempt.objects.filter(user=user, topic=topic).first()


def initial_answers(user, topic) -> dict:
    """Prefill for the quiz form: their saved answers, or their profile default."""
    attempt = get_attempt(user, topic)
    if attempt:
        return {f: getattr(attempt, f) for f in ANSWER_FIELDS}
    # A direct query, so it never reads a stale cached profile. It returns None for
    # users without a profile (e.g. superusers), which falls back to "mix".
    saved = (
        Profile.objects.filter(user=user)
        .values_list("preferred_format", flat=True)
        .first()
    )
    return {"preferred_format": saved or LearningFormat.MIX}