from django.db import transaction
from django.utils import timezone

from .models import Topic, UserTopic

MIN_TOPIC_LENGTH = 2
MAX_TOPIC_LENGTH = 100


class InvalidTopic(ValueError):
    pass


def clean_topic_name(raw: str) -> str:
    """The single source of truth for what a valid topic name is."""
    display = " ".join((raw or "").split())  # trim and collapse whitespace
    if not (MIN_TOPIC_LENGTH <= len(display) <= MAX_TOPIC_LENGTH):
        raise InvalidTopic(
            f"Topic must be {MIN_TOPIC_LENGTH} to {MAX_TOPIC_LENGTH} characters."
        )
    if not any(ch.isalnum() for ch in display):
        raise InvalidTopic("Topic must contain letters or numbers.")
    return display


def get_or_create_topic(*, raw_name: str) -> Topic:
    """Race-safe and idempotent: get_or_create leans on the unique constraint,
    so two simultaneous first searches produce one row, not a crash."""
    display = clean_topic_name(raw_name)
    topic, _ = Topic.objects.get_or_create(
        normalized_name=display.casefold(), defaults={"name": display}
    )
    return topic


def record_search(*, user, raw_name: str) -> Topic:
    with transaction.atomic():  # topic and history: all or nothing
        topic = get_or_create_topic(raw_name=raw_name)
        # update_or_create locks the row while updating, and repeating a search
        # updates the timestamp instead of adding a duplicate row.
        UserTopic.objects.update_or_create(
            user=user, topic=topic, defaults={"last_searched_at": timezone.now()}
        )
    return topic