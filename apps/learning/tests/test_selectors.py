from unittest.mock import patch

from django.test import TestCase

from apps.learning.selectors import NEEDS_QUIZ, NO_PLAN, READY, STALE, get_plan_page
from apps.learning.services import generate_plan

from .helpers import later, make_attempt, make_topic, make_user


class PlanPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user("alice@example.com")
        cls.bob = make_user("bob@example.com")
        cls.topic = make_topic()

    def test_needs_quiz_when_nothing_answered(self):
        page = get_plan_page(self.alice, self.topic)
        self.assertEqual((page.state, page.plan), (NEEDS_QUIZ, None))

    def test_no_plan_after_quiz(self):
        make_attempt(self.alice, self.topic)
        self.assertEqual(get_plan_page(self.alice, self.topic).state, NO_PLAN)

    def test_ready_after_generating(self):
        make_attempt(self.alice, self.topic)
        generate_plan(user=self.alice, topic=self.topic)
        page = get_plan_page(self.alice, self.topic)
        self.assertEqual(page.state, READY)
        self.assertGreater(len(page.plan.steps.all()), 0)

    def test_stale_after_answers_change_and_ready_after_rebuild(self):
        make_attempt(self.alice, self.topic)
        generate_plan(user=self.alice, topic=self.topic)
        with patch("apps.quiz.services.timezone.now", return_value=later()):
            make_attempt(self.alice, self.topic, level="intermediate")
        self.assertEqual(get_plan_page(self.alice, self.topic).state, STALE)
        generate_plan(user=self.alice, topic=self.topic)
        self.assertEqual(get_plan_page(self.alice, self.topic).state, READY)

    def test_users_never_see_each_others_plans(self):
        make_attempt(self.alice, self.topic)
        generate_plan(user=self.alice, topic=self.topic)
        page = get_plan_page(self.bob, self.topic)
        self.assertEqual((page.state, page.plan), (NEEDS_QUIZ, None))

    def test_steps_come_back_in_order_without_extra_queries(self):  # N+1 guard
        make_attempt(self.alice, self.topic)
        generate_plan(user=self.alice, topic=self.topic)
        with self.assertNumQueries(3):  # attempt, plan, all steps together
            page = get_plan_page(self.alice, self.topic)
            positions = [step.position for step in page.plan.steps.all()]
        self.assertEqual(positions, sorted(positions))