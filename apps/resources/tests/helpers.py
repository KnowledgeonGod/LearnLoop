from apps.accounts.services import register_user
from apps.learning.services import generate_plan
from apps.quiz.services import submit_quiz
from apps.topics.services import get_or_create_topic
from services.fetchers import FakeFetcher, FetchError, ResourceDraft

PASSWORD = "S0me-str0ng-pass!"

ANSWERS = {
    "level": "beginner", "goal": "job", "hours_per_week": 6,
    "weeks_available": 8, "preferred_format": "video", "depth": "deep",
}


def make_user(email="ada@example.com"):
    return register_user(name="Test", email=email, password=PASSWORD)


def make_topic(name="Python"):
    return get_or_create_topic(raw_name=name)


def make_plan(user, topic, **overrides):
    # with these answers the fake planner makes 7 steps: 2 read, 2 watch, 3 practice
    submit_quiz(user=user, topic=topic, answers={**ANSWERS, **overrides})
    return generate_plan(user=user, topic=topic)


def draft(**overrides):
    values = dict(kind="video", title="A video", url="https://example.com/v", source="test")
    return ResourceDraft(**{**values, **overrides})


class RecordingFetcher:
    name = "recording"

    def __init__(self):
        self.calls = []

    def find(self, query):
        self.calls.append(query)
        return FakeFetcher().find(query)


class FailingFetcher:
    name = "failing"

    def find(self, query):
        raise FetchError("boom")


class SelectiveFetcher:
    name = "selective"

    def __init__(self, fail_types):
        self.fail_types = set(fail_types)

    def find(self, query):
        if query.step_type in self.fail_types:
            raise FetchError("boom")
        return FakeFetcher().find(query)


class FixedFetcher:
    name = "fixed"

    def __init__(self, drafts):
        self.drafts = drafts

    def find(self, query):
        return self.drafts