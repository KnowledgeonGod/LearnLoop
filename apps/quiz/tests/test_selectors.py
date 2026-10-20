from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.services import set_preferred_format
from apps.quiz.selectors import get_attempt, initial_answers
from apps.quiz.services import submit_quiz

from .helpers import ANSWERS, PASSWORD, make_topic, make_user


class SelectorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user("alice@example.com")
        cls.bob = make_user("bob@example.com")
        cls.topic = make_topic()

    def test_get_attempt_only_returns_own(self):
        submit_quiz(user=self.alice, topic=self.topic, answers=ANSWERS)
        self.assertIsNotNone(get_attempt(self.alice, self.topic))
        self.assertIsNone(get_attempt(self.bob, self.topic))

    def test_initial_uses_saved_answers(self):
        submit_quiz(user=self.alice, topic=self.topic, answers=ANSWERS)
        self.assertEqual(initial_answers(self.alice, self.topic), ANSWERS)

    def test_new_attempt_starts_from_profile_default(self):
        set_preferred_format(user=self.bob, value="reading")
        self.assertEqual(initial_answers(self.bob, self.topic), {"preferred_format": "reading"})

    def test_user_without_profile_falls_back_to_mix(self):  # edge case
        root = get_user_model().objects.create_superuser(
            email="root@example.com", password=PASSWORD, name="Root"
        )
        self.assertEqual(initial_answers(root, self.topic), {"preferred_format": "mix"})