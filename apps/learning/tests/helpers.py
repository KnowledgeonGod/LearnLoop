from datetime import timedelta

from django.utils import timezone

from apps.accounts.services import register_user
from apps.quiz.services import submit_quiz
from apps.topics.services import get_or_create_topic
from services.llm import LLMConfigurationError, LLMError, StepDraft

PASSWORD = "S0me-str0ng-pass!"

ANSWERS = {
    "level": "beginner",
    "goal": "job",
    "hours_per_week": 6,
    "weeks_available": 8,
    "preferred_format": "video",
    "depth": "deep",
}


def make_user(email="ada@example.com"):
    return register_user(name="Test", email=email, password=PASSWORD)


def make_topic(name="Python"):
    return get_or_create_topic(raw_name=name)


def make_attempt(user, topic, **overrides):
    return submit_quiz(user=user, topic=topic, answers={**ANSWERS, **overrides})


def later(hours=1):
    return timezone.now() + timedelta(hours=hours)


def draft(**overrides):
    values = {"title": "A step", "explanation": "Do this.", "minutes": 30, "step_type": "read"}
    return StepDraft(**{**values, "minutes": 30, **overrides})


def valid_drafts(count=4):
    return [draft(title=f"Step {i}") for i in range(count)]


class FailingClient:
    name = "failing"

    def generate_plan(self, request):
        raise LLMError("boom")


class ConfigFailingClient:
    name = "unconfigured"

    def generate_plan(self, request):
        raise LLMConfigurationError("LLM_API_KEY is empty.")


class FixedClient:
    name = "fixed"

    def __init__(self, drafts):
        self.drafts = drafts

    def generate_plan(self, request):
        return self.drafts