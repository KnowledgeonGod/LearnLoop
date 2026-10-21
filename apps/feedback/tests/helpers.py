from apps.accounts.models import Profile
from apps.accounts.services import register_user
from apps.learning.services import generate_plan
from apps.quiz.services import submit_quiz
from apps.resources.models import Resource, StepResource
from apps.resources.services import attach_resources
from apps.topics.services import get_or_create_topic

PASSWORD = "S0me-str0ng-pass!"

ANSWERS = {
    "level": "beginner", "goal": "job", "hours_per_week": 6,
    "weeks_available": 8, "preferred_format": "mix", "depth": "deep",
}


def make_user(email="ada@example.com"):
    return register_user(name="Test", email=email, password=PASSWORD)


def make_topic(name="Python"):
    return get_or_create_topic(raw_name=name)


def make_resource(topic, kind="video", url="https://example.com/a"):
    return Resource.objects.create(topic=topic, kind=kind, title="T", url=url, source="test")


def make_plan_with_resources(user, topic):
    submit_quiz(user=user, topic=topic, answers=ANSWERS)
    plan = generate_plan(user=user, topic=topic)
    attach_resources(plan=plan)
    return plan


def a_resource_of_kind(plan, kind):
    link = StepResource.objects.filter(step__plan=plan, resource__kind=kind).select_related("resource").first()
    return link.resource


def scores(user):
    return Profile.objects.get(user=user).format_scores