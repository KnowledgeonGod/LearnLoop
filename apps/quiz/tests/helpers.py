from apps.accounts.services import register_user
from apps.topics.services import get_or_create_topic

PASSWORD = "S0me-str0ng-pass!"

ANSWERS = {
    "level": "some",
    "goal": "job",
    "hours_per_week": 6,
    "weeks_available": 8,
    "preferred_format": "video",
    "depth": "deep",
}


def make_user(email="ada@example.com"):
    return register_user(name="Test", email=email, password=PASSWORD)  # also creates a profile


def make_topic(name="Python"):
    return get_or_create_topic(raw_name=name)