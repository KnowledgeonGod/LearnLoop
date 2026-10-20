from functools import partial

from django.db import IntegrityError, transaction

from .models import LearningFormat, Profile, User
from .tasks import send_welcome_email


class EmailAlreadyRegistered(Exception):
    pass


def ensure_profile(user: User) -> Profile:
    """Idempotent: calling it twice gives the same single Profile.
    Also covers superusers made with createsuperuser, who have no profile."""
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


def register_user(*, name: str, email: str, password: str) -> User:
    email = email.strip().lower()
    try:
        with transaction.atomic():  # atomicity: user and profile, all or nothing
            user = User.objects.create_user(email=email, password=password, name=name)
            ensure_profile(user)
            # runs only if the transaction commits: no emails for rolled-back signups
            transaction.on_commit(partial(send_welcome_email, user.pk))
    except IntegrityError as exc:
        # Race condition guard: two simultaneous signups both pass any pre-check,
        # the DB unique constraint lets only one through.
        raise EmailAlreadyRegistered(email) from exc
    return user


def set_preferred_format(*, user: User, value: str) -> Profile:
    """Idempotent: setting the same value repeatedly has the same effect as once."""
    if value not in LearningFormat.values:
        raise ValueError(f"Unknown format: {value}")
    profile = ensure_profile(user)
    profile.preferred_format = value
    profile.save(update_fields=["preferred_format"])
    return profile


def record_format_signal(*, user: User, format_name: str, weight: float = 1.0) -> Profile:
    """Add behavior evidence (a video finished, an article skipped...).
    Row locking stops two simultaneous events overwriting each other."""
    if format_name not in LearningFormat.values:
        raise ValueError(f"Unknown format: {format_name}")
    ensure_profile(user)
    with transaction.atomic():
        profile = Profile.objects.select_for_update().get(user=user)
        scores = dict(profile.format_scores)
        scores[format_name] = scores.get(format_name, 0) + weight
        profile.format_scores = scores
        profile.save(update_fields=["format_scores"])
    return profile