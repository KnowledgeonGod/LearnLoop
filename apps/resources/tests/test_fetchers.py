from django.test import SimpleTestCase, override_settings
from unittest.mock import MagicMock, patch

from apps.resources.models import MAX_URL_LENGTH
from apps.resources.services import KINDS_FOR_STEP, clean_drafts
from services.fetchers import (
    FakeFetcher,
    FetchError,
    RealFetcher,
    ResourceDraft,
    ResourceQuery,
    _get_json,
    get_fetcher,
)

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


class FakePaginationTests(SimpleTestCase):
    fetcher = FakeFetcher()

    def test_each_page_returns_new_unique_resources(self):
        for step_type in STEP_TYPES:
            with self.subTest(step_type=step_type):
                first = self.fetcher.find(ResourceQuery("Python", step_type))
                second = self.fetcher.find(ResourceQuery("Python", step_type, page=1))
                self.assertEqual(len(first), len(second))
                self.assertFalse({d.url for d in first} & {d.url for d in second})
                self.assertTrue(all("part 2" in d.title for d in second))
                self.assertEqual(len(clean_drafts(second, KINDS_FOR_STEP[step_type])), len(second))

    def test_page_zero_is_unchanged(self):
        self.assertEqual(
            self.fetcher.find(ResourceQuery("Python", "watch", page=0)),
            self.fetcher.find(ResourceQuery("Python", "watch")),
        )

    def test_negative_pages_are_clamped(self):
        self.assertEqual(
            self.fetcher.find(ResourceQuery("Python", "watch", page=-3)),
            self.fetcher.find(ResourceQuery("Python", "watch")),
        )


@override_settings(YOUTUBE_API_KEY="test-key")
class RealFetcherTests(SimpleTestCase):  # unit tests (network is always mocked)
    fetcher = RealFetcher()

    def find(self, step_type, topic="Python", page=0):
        return self.fetcher.find(ResourceQuery(topic=topic, step_type=step_type, page=page))

    @patch("services.fetchers._get_json")
    def test_watch_maps_youtube_videos(self, get_json):
        get_json.return_value = {
            "items": [
                {"id": {"videoId": "abc123XYZ_-"}, "snippet": {"title": "A real video"}},
                {"id": {"kind": "playlist"}, "snippet": {"title": "not a video"}},
                {"id": {"videoId": "bad id"}, "snippet": {"title": "unsafe id"}},
            ]
        }
        drafts = self.find("watch")
        self.assertEqual(
            drafts,
            [
                ResourceDraft(
                    "video",
                    "A real video",
                    "https://www.youtube.com/watch?v=abc123XYZ_-",
                    "youtube",
                )
            ],
        )
        args, kwargs = get_json.call_args
        self.assertNotIn("key", args[1])  # the key is a header, never a URL parameter
        self.assertEqual(kwargs["headers"]["X-Goog-Api-Key"], "test-key")

    @override_settings(YOUTUBE_API_KEY="")
    def test_watch_without_a_key_is_a_clear_error(self):
        with self.assertRaisesMessage(FetchError, "YOUTUBE_API_KEY"):
            self.find("watch")

    @patch("services.fetchers._get_json")
    def test_read_combines_three_sources(self, get_json):
        get_json.side_effect = [
            {"docs": [{"key": "/works/OL1W", "title": "A real book"}]},
            {"query": {"search": [{"title": "Python (programming language)"}]}},
            {"items": [{"id": "GB1", "volumeInfo": {"title": "A Google book"}}]},
        ]
        drafts = self.find("read")
        self.assertEqual([d.kind for d in drafts], ["book", "article", "book"])
        self.assertEqual(drafts[0].url, "https://openlibrary.org/works/OL1W")
        self.assertEqual(
            drafts[1].url, "https://en.wikipedia.org/wiki/Python_%28programming_language%29"
        )
        self.assertEqual(drafts[2].url, "https://books.google.com/books?id=GB1")
        self.assertEqual(drafts[2].source, "googlebooks")

    @patch("services.fetchers._get_json")
    def test_read_survives_one_source_being_down(self, get_json):
        get_json.side_effect = [
            FetchError("open library down"),
            {"query": {"search": [{"title": "Still useful"}]}},
            {"items": []},
        ]
        drafts = self.find("read")
        self.assertEqual([d.title for d in drafts], ["Still useful"])

    @patch("services.fetchers._get_json")
    def test_read_fails_only_when_all_sources_fail(self, get_json):
        get_json.side_effect = [FetchError("one"), FetchError("two"), FetchError("three")]
        with self.assertRaises(FetchError):
            self.find("read")

    @patch("services.fetchers._get_json")
    def test_google_books_maps_volumes_and_skips_bad_links(self, get_json):
        get_json.return_value = {
            "items": [
                {
                    "id": "GB1",
                    "volumeInfo": {
                        "title": "A Google book",
                        "infoLink": "https://books.google.com/books/about/GB1.html",
                    },
                },
                {"id": "bad id", "volumeInfo": {"title": "Unsafe id"}},
                {"id": "GB2", "volumeInfo": {"title": ""}},
                "not a dict",
            ]
        }
        drafts = self.fetcher._googlebooks("Python", 0, 5)
        self.assertEqual(
            drafts,
            [
                ResourceDraft(
                    "book",
                    "A Google book",
                    "https://books.google.com/books/about/GB1.html",
                    "googlebooks",
                )
            ],
        )

    @patch("services.fetchers._get_json")
    def test_google_books_without_items_is_empty(self, get_json):
        get_json.return_value = {"totalItems": 0}
        self.assertEqual(self.fetcher._googlebooks("Python", 0, 5), [])

    @patch("services.fetchers._get_json")
    def test_google_books_malformed_payload_is_an_error(self, get_json):
        get_json.return_value = {"items": "nope"}
        with self.assertRaises(FetchError):
            self.fetcher._googlebooks("Python", 0, 5)

    @patch("services.fetchers._get_json")
    def test_practice_maps_github_repositories(self, get_json):
        get_json.return_value = {
            "items": [
                {"full_name": "ada/exercises", "html_url": "https://github.com/ada/exercises"},
                {"full_name": "bad", "html_url": "https://evil.example.com/x"},
            ]
        }
        drafts = self.find("practice")
        self.assertEqual(
            drafts,
            [ResourceDraft("exercise", "ada/exercises", "https://github.com/ada/exercises", "github")],
        )

    @patch("services.fetchers._get_json")
    def test_page_is_forwarded_to_every_read_source(self, get_json):
        get_json.return_value = {"query": {"search": []}, "docs": [], "items": []}
        self.find("read", page=2)
        offsets = [call.args[1] for call in get_json.call_args_list]
        self.assertEqual(offsets[0]["page"], 3)  # Open Library is 1-based
        self.assertEqual(offsets[1]["sroffset"], 6)  # Wikipedia: page * per-source limit
        self.assertEqual(offsets[2]["startIndex"], 6)  # Google Books is 0-based

    @patch("services.fetchers._get_json")
    def test_results_are_deduplicated(self, get_json):
        get_json.return_value = {
            "items": [
                {"full_name": "a/b", "html_url": "https://github.com/a/b"},
                {"full_name": "a/b again", "html_url": "https://github.com/a/b"},
            ]
        }
        self.assertEqual(len(self.find("practice")), 1)

    def test_network_errors_become_fetch_errors(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = OSError("offline")
        with patch("services.fetchers.urlopen", return_value=response):
            with self.assertRaises(FetchError):
                _get_json("https://example.com/api", {"q": "Python"})

    def test_invalid_json_becomes_a_fetch_error(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"not json"
        with patch("services.fetchers.urlopen", return_value=response):
            with self.assertRaises(FetchError):
                _get_json("https://example.com/api", {"q": "Python"})

    def test_non_object_json_is_rejected(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"[]"
        with patch("services.fetchers.urlopen", return_value=response):
            with self.assertRaises(FetchError):
                _get_json("https://example.com/api", {"q": "Python"})

    def test_a_valid_json_object_is_returned(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"ok": true}'
        with patch("services.fetchers.urlopen", return_value=response):
            self.assertEqual(_get_json("https://example.com/api", {"q": "Python"}), {"ok": True})

    @patch("services.fetchers._get_json")
    def test_malformed_api_payloads_become_fetch_errors(self, get_json):
        cases = {
            "watch": {"items": "nope"},
            "read": {"docs": "nope", "query": {"search": []}},
            "practice": {"items": "nope"},
        }
        for step_type, payload in cases.items():
            with self.subTest(step_type=step_type):
                get_json.return_value = payload
                with self.assertRaises(FetchError):
                    self.find(step_type)

    @patch("services.fetchers._get_json")
    def test_non_dict_items_are_skipped(self, get_json):
        get_json.side_effect = [
            {"items": [1, {"id": {"videoId": "abc12"}, "snippet": {"title": "Ok"}}]},
            {"docs": [1, {"key": "/works/OL1W", "title": "Book"}]},
            {"query": {"search": [1, {"title": "Article"}]}},
            {"items": [1, {"id": "GB1", "volumeInfo": {"title": "Google book"}}]},
            {"items": [1, {"full_name": "a/b", "html_url": "https://github.com/a/b"}]},
        ]
        self.assertEqual(len(self.find("watch")), 1)
        self.assertEqual(len(self.find("read")), 3)
        self.assertEqual(len(self.find("practice")), 1)

    @patch("services.fetchers._get_json")
    def test_wikipedia_malformed_payload_is_an_error(self, get_json):
        get_json.side_effect = [{"docs": []}, {"query": "nope"}, {"items": []}]
        with self.assertRaises(FetchError):
            self.find("read")

    @override_settings(GITHUB_TOKEN="gh-token")
    @patch("services.fetchers._get_json")
    def test_github_token_is_sent_when_configured(self, get_json):
        get_json.return_value = {"items": []}
        self.find("practice")
        self.assertEqual(get_json.call_args.kwargs["headers"]["Authorization"], "Bearer gh-token")

    def test_an_unknown_step_type_is_rejected(self):
        with self.assertRaises(FetchError):
            self.find("listen")


class GetFetcherTests(SimpleTestCase):
    def test_default_is_the_fake_fetcher(self):
        self.assertIsInstance(get_fetcher(), FakeFetcher)

    @override_settings(RESOURCE_BACKEND="real")
    def test_real_backend_is_selectable(self):
        self.assertIsInstance(get_fetcher(), RealFetcher)

    @override_settings(RESOURCE_BACKEND="nope")
    def test_unknown_backend_raises(self):
        with self.assertRaises(FetchError):
            get_fetcher()