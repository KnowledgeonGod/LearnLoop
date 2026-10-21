from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from apps.feedback.models import ResourceFeedback
from apps.feedback.services import ACTIONS, InvalidFeedback, apply_action, contribution, set_feedback

from .helpers import PASSWORD, make_resource, make_topic, make_user, scores


class PureLogicTests(SimpleTestCase):  # unit tests
    def test_contribution_adds_rating_and_progress(self):
        self.assertEqual(contribution("like", "done"), 3.0)
        self.assertEqual(contribution("dislike", "skipped"), -1.5)
        self.assertEqual(contribution("like", None), 1.0)
        self.assertEqual(contribution(None, None), 0.0)

    def test_each_action_changes_only_its_own_field(self):
        self.assertEqual(apply_action(None, "done", "like"), ("like", "done"))
        self.assertEqual(apply_action("like", None, "done"), ("like", "done"))
        self.assertEqual(apply_action("like", "done", "clear_rating"), (None, "done"))
        self.assertEqual(apply_action("like", "done", "clear_progress"), ("like", None))
        self.assertEqual(apply_action("like", None, "dislike"), ("dislike", None))
        self.assertEqual(apply_action(None, "done", "skip"), (None, "skipped"))

    def test_unknown_action_raises(self):
        with self.assertRaises(InvalidFeedback):
            apply_action(None, None, "explode")

    def test_every_listed_action_is_handled(self):
        for action in ACTIONS:
            apply_action(None, None, action)


class SetFeedbackTests(TestCase):
    @classmethod
    def setUpTestData(cls):  # fixture
        cls.user = make_user()
        cls.topic = make_topic()
        cls.video = make_resource(cls.topic, "video", "https://example.com/v")

    def act(self, action, user=None, resource=None):
        return set_feedback(user=user or self.user, resource=resource or self.video, action=action)

    def test_like_saves_and_records_a_format_signal(self):
        row = self.act("like")
        self.assertEqual(row.rating, "like")
        self.assertEqual(scores(self.user)["video"], 1.0)

    def test_repeating_an_action_changes_nothing(self):  # idempotent
        self.act("like")
        self.act("like")
        self.assertEqual(ResourceFeedback.objects.count(), 1)
        self.assertEqual(scores(self.user)["video"], 1.0)

    def test_switching_rating_replaces_the_old_signal(self):
        self.act("like")
        self.act("dislike")
        self.assertEqual(ResourceFeedback.objects.get().rating, "dislike")
        self.assertEqual(scores(self.user)["video"], -1.0)

    def test_switching_progress_replaces_the_old_signal(self):
        self.act("done")
        self.act("skip")
        self.assertEqual(scores(self.user)["video"], -0.5)

    def test_clearing_the_only_field_deletes_the_row_and_reverses_the_signal(self):
        self.act("like")
        self.assertIsNone(self.act("clear_rating"))
        self.assertEqual(ResourceFeedback.objects.count(), 0)
        self.assertEqual(scores(self.user)["video"], 0)

    def test_rating_and_progress_are_independent(self):
        self.act("like")
        self.act("done")
        self.assertEqual(ResourceFeedback.objects.count(), 1)
        self.assertEqual(scores(self.user)["video"], 3.0)
        self.act("clear_rating")
        row = ResourceFeedback.objects.get()
        self.assertEqual((row.rating, row.progress), (None, "done"))
        self.assertEqual(scores(self.user)["video"], 2.0)

    def test_clearing_something_never_set_is_harmless(self):  # edge case
        self.assertIsNone(self.act("clear_rating"))
        self.assertEqual(ResourceFeedback.objects.count(), 0)
        self.assertEqual(scores(self.user), {})

    def test_each_kind_feeds_the_matching_format(self):
        expected = {"video": "video", "book": "reading", "article": "reading", "exercise": "practice"}
        for i, (kind, fmt) in enumerate(expected.items()):
            with self.subTest(kind=kind):
                user = make_user(f"u{i}@example.com")
                resource = make_resource(self.topic, kind, f"https://example.com/k{i}")
                self.act("like", user=user, resource=resource)
                self.assertEqual(scores(user), {fmt: 1.0})

    def test_users_are_independent(self):
        bob = make_user("bob@example.com")
        self.act("like")
        self.assertEqual(scores(bob), {})
        self.assertEqual(ResourceFeedback.objects.filter(user=bob).count(), 0)

    def test_unknown_action_saves_nothing(self):
        with self.assertRaises(InvalidFeedback):
            self.act("explode")
        self.assertEqual(ResourceFeedback.objects.count(), 0)
        self.assertEqual(scores(self.user), {})

    def test_failure_while_recording_the_signal_rolls_back_the_feedback(self):  # atomicity, via mocking
        with patch("apps.feedback.services.record_format_signal", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.act("like")
        self.assertEqual(ResourceFeedback.objects.count(), 0)

    def test_user_without_a_profile_still_works(self):  # edge case
        root = get_user_model().objects.create_superuser(
            email="root@example.com", password=PASSWORD, name="Root"
        )
        self.act("like", user=root)
        self.assertEqual(scores(root)["video"], 1.0)


class ConstraintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.resource = make_resource(make_topic())

    def make(self, **overrides):
        values = dict(user=self.user, resource=self.resource, rating="like", progress=None)
        return ResourceFeedback.objects.create(**{**values, **overrides})

    def test_database_rejects_invalid_values(self):
        bad = [{"rating": "nonsense"}, {"progress": "nonsense"}, {"rating": None, "progress": None}]
        for overrides in bad:
            with self.subTest(overrides=overrides), self.assertRaises(IntegrityError), transaction.atomic():
                self.make(**overrides)

    def test_either_field_alone_is_allowed(self):
        self.make(rating="like", progress=None)
        other = make_resource(make_topic("Rust"), "video", "https://example.com/r")
        self.make(resource=other, rating=None, progress="done")
        self.assertEqual(ResourceFeedback.objects.count(), 2)

    def test_database_rejects_a_duplicate_row(self):
        self.make()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make(rating="dislike")