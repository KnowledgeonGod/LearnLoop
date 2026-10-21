from django.db import transaction

from apps.accounts.models import Profile
from apps.accounts.services import ensure_profile, record_format_signal
from apps.resources.models import ResourceKind

from .models import Progress, Rating, ResourceFeedback

# All the numbers in one place. They are exactly representable as floats,
# so adding and removing them never leaves rounding dust.
RATING_WEIGHT = {Rating.LIKE.value: 1.0, Rating.DISLIKE.value: -1.0}
PROGRESS_WEIGHT = {Progress.DONE.value: 2.0, Progress.SKIPPED.value: -0.5}

FORMAT_FOR_KIND = {
    ResourceKind.VIDEO.value: "video",
    ResourceKind.BOOK.value: "reading",
    ResourceKind.ARTICLE.value: "reading",
    ResourceKind.EXERCISE.value: "practice",
}

ACTIONS = ("like", "dislike", "clear_rating", "done", "skip", "clear_progress")


class InvalidFeedback(ValueError):
    pass


def contribution(rating, progress) -> float:
    """How much a feedback state is worth to the profile."""
    return RATING_WEIGHT.get(rating, 0.0) + PROGRESS_WEIGHT.get(progress, 0.0)


def apply_action(rating, progress, action):
    """Pure logic: (rating, progress) after the action. Each action touches one field."""
    if action == "like":
        return Rating.LIKE.value, progress
    if action == "dislike":
        return Rating.DISLIKE.value, progress
    if action == "clear_rating":
        return None, progress
    if action == "done":
        return rating, Progress.DONE.value
    if action == "skip":
        return rating, Progress.SKIPPED.value
    if action == "clear_progress":
        return rating, None
    raise InvalidFeedback(action)


def set_feedback(*, user, resource, action):
    """Returns the feedback row, or None if nothing is left after the action."""
    if action not in ACTIONS:
        raise InvalidFeedback(action)

    with transaction.atomic():  # feedback row and profile signal: all or nothing
        # Row locking: a user's feedback changes queue up on their profile row, so two
        # quick clicks can't both read the same "before" state and double count.
        profile = ensure_profile(user)
        Profile.objects.select_for_update().get(pk=profile.pk)

        existing = ResourceFeedback.objects.filter(user=user, resource=resource).first()
        old_rating = existing.rating if existing else None
        old_progress = existing.progress if existing else None
        rating, progress = apply_action(old_rating, old_progress, action)

        if (rating, progress) == (old_rating, old_progress):
            return existing  # idempotent: repeating an action changes nothing

        if rating is None and progress is None:
            existing.delete()
            result = None
        elif existing:
            existing.rating, existing.progress = rating, progress
            existing.save(update_fields=["rating", "progress", "updated_at"])
            result = existing
        else:
            result = ResourceFeedback.objects.create(
                user=user, resource=resource, rating=rating, progress=progress
            )

        # Only the DIFFERENCE reaches the profile, so switching like to dislike
        # reverses the old signal and undoing removes it.
        delta = contribution(rating, progress) - contribution(old_rating, old_progress)
        if delta:
            record_format_signal(
                user=user, format_name=FORMAT_FOR_KIND[resource.kind], weight=delta
            )
    return result