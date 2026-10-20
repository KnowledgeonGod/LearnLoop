from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from apps.topics.models import Topic, UserTopic
from apps.topics.services import (
    InvalidTopic,
    clean_topic_name,
    get_or_create_topic,
    record_search,
)

from .helpers import make_user, search_at


class CleanTopicNameTests(SimpleTestCase):  # unit tests
    def test_collapses_whitespace(self):
        self.assertEqual(clean_topic_name("  Machine   Learning "), "Machine Learning")

    def test_too_short_rejected(self):
        with self.assertRaises(InvalidTopic):
            clean_topic_name("a")

    def test_too_long_rejected(self):
        with self.assertRaises(InvalidTopic):
            clean_topic_name("x" * 101)

    def test_symbols_only_rejected(self):
        with self.assertRaises(InvalidTopic):
            clean_topic_name("!!! ???")

    def test_non_latin_names_allowed(self):  # edge case
        self.assertEqual(clean_topic_name("日本語"), "日本語")


class GetOrCreateTopicTests(TestCase):
    def test_case_and_spacing_variants_share_one_topic(self):
        ids = {get_or_create_topic(raw_name=n).pk for n in ("Python", " python  ", "PYTHON")}
        self.assertEqual(len(ids), 1)
        self.assertEqual(Topic.objects.count(), 1)

    def test_first_display_name_wins(self):
        get_or_create_topic(raw_name="Python")
        self.assertEqual(get_or_create_topic(raw_name="PYTHON").name, "Python")

    def test_invalid_name_creates_nothing(self):
        with self.assertRaises(InvalidTopic):
            get_or_create_topic(raw_name="?")
        self.assertEqual(Topic.objects.count(), 0)

    def test_database_rejects_duplicate_normalized_name(self):
        Topic.objects.create(name="Go", normalized_name="go")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Topic.objects.create(name="GO", normalized_name="go")

    def test_database_rejects_blank_normalized_name(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Topic.objects.create(name="x", normalized_name="")


class RecordSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):  # fixture
        cls.user = make_user()

    def test_repeating_a_search_updates_instead_of_duplicating(self):  # idempotent
        search_at(self.user, "Python", hours=0)
        search_at(self.user, "python", hours=5)
        self.assertEqual(UserTopic.objects.count(), 1)
        entry = UserTopic.objects.get()
        first = search_at(self.user, "Rust", hours=1)  # a different topic, a second row
        self.assertEqual(UserTopic.objects.count(), 2)
        entry.refresh_from_db()
        self.assertGreater(entry.last_searched_at, UserTopic.objects.get(topic=first).last_searched_at)

    def test_invalid_search_records_nothing(self):
        with self.assertRaises(InvalidTopic):
            record_search(user=self.user, raw_name="!")
        self.assertEqual(UserTopic.objects.count(), 0)

    @patch("apps.topics.services.UserTopic.objects.update_or_create", side_effect=RuntimeError)
    def test_failure_rolls_back_the_new_topic(self, _mock):  # atomicity, via mocking
        with self.assertRaises(RuntimeError):
            record_search(user=self.user, raw_name="Python")
        self.assertEqual(Topic.objects.count(), 0)

    def test_database_rejects_duplicate_history_row(self):
        topic = search_at(self.user, "Python")
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserTopic.objects.create(user=self.user, topic=topic)