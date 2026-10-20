from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.learning.models import LearningPlan, PlanStep
from apps.learning.services import (
    InvalidPlan,
    PlanGenerationFailed,
    QuizRequired,
    generate_plan,
    validate_drafts,
)

from .helpers import FailingClient, FixedClient, draft, make_attempt, make_topic, make_user, valid_drafts


class ValidateDraftsTests(SimpleTestCase):  # unit tests
    def test_valid_drafts_are_cleaned(self):
        drafts = valid_drafts()
        drafts[0] = draft(title="  Spaced   title ", explanation="  text  ")
        cleaned = validate_drafts(drafts)
        self.assertEqual(cleaned[0], ("Spaced title", "text", 30, "read"))

    def test_bad_step_counts_rejected(self):  # edge cases
        for count in (0, 3, 9):
            with self.subTest(count=count), self.assertRaises(InvalidPlan):
                validate_drafts(valid_drafts(count))

    def test_boundary_step_counts_accepted(self):
        for count in (4, 8):
            self.assertEqual(len(validate_drafts(valid_drafts(count))), count)

    def test_not_a_list_rejected(self):
        with self.assertRaises(InvalidPlan):
            validate_drafts(None)

    def test_bad_steps_rejected(self):
        bad_overrides = [
            {"title": None}, {"title": "   "}, {"title": "x" * 201},
            {"explanation": ""}, {"explanation": 5}, {"explanation": "x" * 1001},
            {"step_type": "listen"}, {"step_type": None},
            {"minutes": 4}, {"minutes": 481}, {"minutes": True}, {"minutes": "30"}, {"minutes": None},
        ]
        for overrides in bad_overrides:
            with self.subTest(overrides=overrides), self.assertRaises(InvalidPlan):
                validate_drafts(valid_drafts(3) + [draft(**overrides)])

    def test_object_without_the_right_fields_rejected(self):
        with self.assertRaises(InvalidPlan):
            validate_drafts(valid_drafts(3) + [object()])


class GeneratePlanTests(TestCase):
    @classmethod
    def setUpTestData(cls):  # fixture
        cls.user = make_user()
        cls.topic = make_topic()

    def test_requires_a_quiz_first(self):
        with self.assertRaises(QuizRequired):
            generate_plan(user=self.user, topic=self.topic)
        self.assertEqual(LearningPlan.objects.count(), 0)

    def test_creates_plan_with_ordered_steps(self):
        attempt = make_attempt(self.user, self.topic)
        plan = generate_plan(user=self.user, topic=self.topic)
        steps = list(plan.steps.all())
        self.assertEqual([s.position for s in steps], list(range(1, len(steps) + 1)))
        self.assertEqual(plan.source, "fake")
        self.assertEqual(plan.based_on_quiz_at, attempt.updated_at)
        self.assertIn(self.topic.name, str(plan))
        self.assertIn(".", str(steps[0]))

    def test_plan_reflects_the_quiz_answers(self):
        make_attempt(self.user, self.topic, level="beginner", depth="deep", goal="exam")
        long_plan = generate_plan(user=self.user, topic=self.topic)
        self.assertGreater(long_plan.steps.count(), 5)

    def test_regenerating_is_idempotent(self):
        make_attempt(self.user, self.topic)
        first = generate_plan(user=self.user, topic=self.topic)
        titles = [s.title for s in first.steps.all()]
        second = generate_plan(user=self.user, topic=self.topic)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(LearningPlan.objects.count(), 1)
        self.assertEqual([s.title for s in second.steps.all()], titles)

    def test_changed_answers_replace_the_steps(self):
        make_attempt(self.user, self.topic, level="beginner", depth="deep")
        before = generate_plan(user=self.user, topic=self.topic).steps.count()
        make_attempt(self.user, self.topic, level="intermediate", depth="overview", goal="curiosity")
        after = generate_plan(user=self.user, topic=self.topic).steps.count()
        self.assertLess(after, before)
        self.assertEqual(PlanStep.objects.count(), after)  # no leftover steps

    def test_llm_failure_creates_nothing(self):
        make_attempt(self.user, self.topic)
        with self.assertRaises(PlanGenerationFailed):
            generate_plan(user=self.user, topic=self.topic, client=FailingClient())
        self.assertEqual(LearningPlan.objects.count(), 0)

    def test_llm_failure_keeps_the_existing_plan(self):
        make_attempt(self.user, self.topic)
        plan = generate_plan(user=self.user, topic=self.topic)
        count = plan.steps.count()
        with self.assertRaises(PlanGenerationFailed):
            generate_plan(user=self.user, topic=self.topic, client=FailingClient())
        self.assertEqual(plan.steps.count(), count)

    def test_invalid_llm_output_saves_nothing(self):
        make_attempt(self.user, self.topic)
        with self.assertRaises(InvalidPlan):
            generate_plan(user=self.user, topic=self.topic, client=FixedClient(valid_drafts(2)))
        self.assertEqual(LearningPlan.objects.count(), 0)

    def test_custom_client_name_is_recorded(self):
        make_attempt(self.user, self.topic)
        plan = generate_plan(user=self.user, topic=self.topic, client=FixedClient(valid_drafts()))
        self.assertEqual(plan.source, "fixed")

    @patch("apps.learning.services.PlanStep.objects.bulk_create", side_effect=RuntimeError)
    def test_failure_while_saving_rolls_back_everything(self, _mock):  # atomicity, via mocking
        make_attempt(self.user, self.topic)
        with self.assertRaises(RuntimeError):
            generate_plan(user=self.user, topic=self.topic)
        self.assertEqual(LearningPlan.objects.count(), 0)

    def test_failure_while_rebuilding_keeps_the_old_steps(self):
        make_attempt(self.user, self.topic)
        plan = generate_plan(user=self.user, topic=self.topic)
        count = plan.steps.count()
        make_attempt(self.user, self.topic, level="intermediate", depth="overview")
        with patch("apps.learning.services.PlanStep.objects.bulk_create", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                generate_plan(user=self.user, topic=self.topic)
        self.assertEqual(plan.steps.count(), count)  # the delete was rolled back too

    def test_each_user_gets_their_own_plan(self):
        bob = make_user("bob@example.com")
        make_attempt(self.user, self.topic, goal="exam")
        make_attempt(bob, self.topic, goal="job")
        generate_plan(user=self.user, topic=self.topic)
        generate_plan(user=bob, topic=self.topic)
        self.assertEqual(LearningPlan.objects.count(), 2)


class PlanConstraintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = make_user()
        topic = make_topic()
        cls.plan = LearningPlan.objects.create(
            user=user, topic=topic, based_on_quiz_at=timezone.now(), source="test"
        )
        cls.user, cls.topic = user, topic

    def make_step(self, **overrides):
        values = dict(plan=self.plan, position=1, title="T", explanation="E", minutes=30, step_type="read")
        return PlanStep.objects.create(**{**values, **overrides})

    def test_database_rejects_bad_steps(self):
        bad = [
            {"position": 0}, {"position": 9}, {"minutes": 4}, {"minutes": 481},
            {"step_type": "nonsense"}, {"title": ""},
        ]
        for overrides in bad:
            with self.subTest(overrides=overrides), self.assertRaises(IntegrityError), transaction.atomic():
                self.make_step(**overrides)

    def test_database_rejects_duplicate_position(self):
        self.make_step(position=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_step(position=1)

    def test_database_rejects_duplicate_plan(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            LearningPlan.objects.create(
                user=self.user, topic=self.topic, based_on_quiz_at=timezone.now(), source="x"
            )