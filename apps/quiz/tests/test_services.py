from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from apps.accounts.models import Profile
from apps.quiz.models import QuizAttempt
from apps.quiz.services import InvalidQuiz, submit_quiz, validate_answers

from .helpers import ANSWERS, make_topic, make_user


class ValidateAnswersTests(SimpleTestCase):  # unit tests
    def test_valid_answers_are_returned_cleaned(self):
        cleaned = validate_answers({**ANSWERS, "hours_per_week": "6"})  # text from a form
        self.assertEqual(cleaned["hours_per_week"], 6)

    def test_missing_answer_rejected(self):
        incomplete = {k: v for k, v in ANSWERS.items() if k != "goal"}
        with self.assertRaises(InvalidQuiz):
            validate_answers(incomplete)

    def test_invalid_choice_rejected(self):
        with self.assertRaises(InvalidQuiz):
            validate_answers({**ANSWERS, "level": "wizard"})

    def test_hours_out_of_range_rejected(self):  # edge cases
        for hours in (0, 41, -3):
            with self.subTest(hours=hours), self.assertRaises(InvalidQuiz):
                validate_answers({**ANSWERS, "hours_per_week": hours})

    def test_weeks_out_of_range_rejected(self):
        for weeks in (0, 53):
            with self.subTest(weeks=weeks), self.assertRaises(InvalidQuiz):
                validate_answers({**ANSWERS, "weeks_available": weeks})

    def test_non_numeric_hours_rejected(self):
        with self.assertRaises(InvalidQuiz):
            validate_answers({**ANSWERS, "hours_per_week": "abc"})

    def test_unknown_answer_rejected(self):
        with self.assertRaises(InvalidQuiz):
            validate_answers({**ANSWERS, "is_admin": True})


class SubmitQuizTests(TestCase):
    @classmethod
    def setUpTestData(cls):  # fixture
        cls.user = make_user()
        cls.topic = make_topic()

    def test_saves_attempt_and_copies_format_to_profile(self):
        attempt = submit_quiz(user=self.user, topic=self.topic, answers=ANSWERS)
        self.assertEqual(attempt.goal, "job")
        self.assertEqual(Profile.objects.get(user=self.user).preferred_format, "video")
        self.assertIn(self.topic.name, str(attempt))

    def test_retaking_updates_instead_of_duplicating(self):
        submit_quiz(user=self.user, topic=self.topic, answers=ANSWERS)
        submit_quiz(user=self.user, topic=self.topic, answers={**ANSWERS, "level": "intermediate"})
        self.assertEqual(QuizAttempt.objects.count(), 1)
        self.assertEqual(QuizAttempt.objects.get().level, "intermediate")

    def test_submitting_same_answers_twice_is_idempotent(self):
        submit_quiz(user=self.user, topic=self.topic, answers=ANSWERS)
        submit_quiz(user=self.user, topic=self.topic, answers=ANSWERS)
        self.assertEqual(QuizAttempt.objects.count(), 1)

    def test_invalid_answers_save_nothing_and_leave_profile_alone(self):
        with self.assertRaises(InvalidQuiz):
            submit_quiz(user=self.user, topic=self.topic, answers={**ANSWERS, "hours_per_week": 0})
        self.assertEqual(QuizAttempt.objects.count(), 0)
        self.assertEqual(Profile.objects.get(user=self.user).preferred_format, "mix")

    @patch("apps.quiz.services.set_preferred_format", side_effect=RuntimeError)
    def test_failure_rolls_back_the_attempt(self, _mock):  # atomicity, via mocking
        with self.assertRaises(RuntimeError):
            submit_quiz(user=self.user, topic=self.topic, answers=ANSWERS)
        self.assertEqual(QuizAttempt.objects.count(), 0)

    def test_each_user_has_their_own_answers_for_a_topic(self):
        other = make_user("bob@example.com")
        submit_quiz(user=self.user, topic=self.topic, answers=ANSWERS)
        submit_quiz(user=other, topic=self.topic, answers={**ANSWERS, "goal": "exam"})
        self.assertEqual(QuizAttempt.objects.count(), 2)
        self.assertEqual(QuizAttempt.objects.get(user=self.user).goal, "job")

    def test_database_rejects_invalid_choice(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            QuizAttempt.objects.create(
                user=self.user, topic=self.topic, **{**ANSWERS, "level": "nonsense"}
            )

    def test_database_rejects_hours_out_of_range(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            QuizAttempt.objects.create(
                user=self.user, topic=self.topic, **{**ANSWERS, "hours_per_week": 0}
            )

    def test_database_rejects_duplicate_attempt(self):
        QuizAttempt.objects.create(user=self.user, topic=self.topic, **ANSWERS)
        with self.assertRaises(IntegrityError), transaction.atomic():
            QuizAttempt.objects.create(user=self.user, topic=self.topic, **ANSWERS)