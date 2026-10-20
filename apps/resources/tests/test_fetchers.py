from django.test import SimpleTestCase, override_settings

from apps.resources.models import MAX_URL_LENGTH
from apps.resources.services import KINDS_FOR_STEP, clean_drafts
from services.fetchers import FakeFetcher, FetchError, ResourceQuery, get_fetcher

STEP_TYPES = ("watch", "read", "practice")


class FakeFetcherTests(SimpleTestCase):  # unit tests
    fetcher = FakeFetcher()

    def find(self, step_type, topic="Python"):
        return self.fetcher.find(ResourceQuery(topic=topic, step_type=step_type))

    def test_every_step_type_gives_usable_resources(self):
        # The fake must never produce output our own validator would drop.
        for step_type in STEP_TYPES:
            with self.subTest(step_type=step_type):
                drafts = self.find(step_type)
                self.assertGreaterEqual(len(drafts), 4)
                self.assertEqual(len(clean_drafts(drafts, KINDS_FOR_STEP[step_type])), len(drafts))

    def test_kinds_match_the_step_type(self):
        for step_type in STEP_TYPES:
            for item in self.find(step_type):
                self.assertIn(item.kind, KINDS_FOR_STEP[step_type])

    def test_urls_are_unique(self):
        for step_type in STEP_TYPES:
            urls = [d.url for d in self.find(step_type)]
            self.assertEqual(len(urls), len(set(urls)))

    def test_is_deterministic(self):
        self.assertEqual(self.find("watch"), self.find("watch"))

    def test_longest_non_latin_topic_still_fits(self):  # edge case
        topic = "日" * 100
        for step_type in STEP_TYPES:
            drafts = self.find(step_type, topic)
            self.assertEqual(len(clean_drafts(drafts, KINDS_FOR_STEP[step_type])), len(drafts))
            self.assertLessEqual(max(len(d.url) for d in drafts), MAX_URL_LENGTH)

    def test_special_characters_are_encoded(self):
        for step_type in STEP_TYPES:
            for item in self.find(step_type, "C++ & C#"):
                self.assertNotIn(" ", item.url)
                self.assertNotIn("#", item.url)  # no accidental URL fragment

    def test_topic_text_cannot_inject_url_parameters(self):  # security
        for step_type in STEP_TYPES:
            for item in self.find(step_type, "x&evil=1"):
                self.assertNotIn("&evil", item.url)

    def test_unknown_step_type_raises(self):
        with self.assertRaises(FetchError):
            self.find("listen")


class GetFetcherTests(SimpleTestCase):
    def test_default_is_the_fake_fetcher(self):
        self.assertIsInstance(get_fetcher(), FakeFetcher)

    @override_settings(RESOURCE_BACKEND="nope")
    def test_unknown_backend_raises(self):
        with self.assertRaises(FetchError):
            get_fetcher()