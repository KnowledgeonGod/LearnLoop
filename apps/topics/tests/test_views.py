from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.topics.models import Topic, UserTopic

from .helpers import make_user, search_at


@override_settings(RATELIMIT_ENABLE=False)
class TopicViewTests(TestCase):  # integration tests
    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user("alice@example.com")
        cls.bob = make_user("bob@example.com")

    def setUp(self):
        self.client.force_login(self.alice)

    def test_pages_require_login(self):
        self.client.logout()
        for resp in (
            self.client.get(reverse("dashboard")),
            self.client.get(reverse("topic_detail", args=[1])),
            self.client.post(reverse("topic_search"), {"query": "Python"}),
        ):
            self.assertEqual(resp.status_code, 302)
            self.assertIn(reverse("login"), resp.url)

    def test_search_creates_topic_and_history_then_redirects(self):
        resp = self.client.post(reverse("topic_search"), {"query": "Python"})
        topic = Topic.objects.get()
        self.assertRedirects(resp, reverse("topic_detail", args=[topic.pk]))
        self.assertTrue(UserTopic.objects.filter(user=self.alice, topic=topic).exists())

    def test_case_variants_reuse_one_topic(self):
        self.client.post(reverse("topic_search"), {"query": "Python"})
        self.client.post(reverse("topic_search"), {"query": "  python "})
        self.assertEqual(Topic.objects.count(), 1)

    def test_search_is_post_only(self):
        self.assertEqual(self.client.get(reverse("topic_search")).status_code, 405)

    def test_invalid_query_returns_400_and_creates_nothing(self):
        resp = self.client.post(reverse("topic_search"), {"query": "!!"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Topic.objects.count(), 0)

    def test_unknown_topic_is_404(self):
        self.assertEqual(self.client.get(reverse("topic_detail", args=[99999])).status_code, 404)

    def test_dashboard_shows_only_own_history(self):
        search_at(self.bob, "BobPrivate")
        search_at(self.alice, "AliceTopic")
        resp = self.client.get(reverse("dashboard"))
        self.assertContains(resp, "AliceTopic")
        self.assertNotContains(resp, "BobPrivate")

    def test_topic_names_are_escaped(self):  # XSS
        self.client.post(reverse("topic_search"), {"query": "<b>x</b>"})
        resp = self.client.get(reverse("dashboard"))
        self.assertNotContains(resp, "<b>x</b>")
        self.assertContains(resp, "&lt;b&gt;x&lt;/b&gt;")

    def test_sql_injection_text_is_stored_as_plain_text(self):
        evil = "x'; DROP TABLE topics_topic; --"
        self.client.post(reverse("topic_search"), {"query": evil})
        self.assertEqual(Topic.objects.count(), 1)
        self.assertEqual(Topic.objects.get().name, evil)

    def test_search_without_csrf_token_is_rejected(self):  # CSRF
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.alice)
        resp = client.post(reverse("topic_search"), {"query": "Python"})
        self.assertEqual(resp.status_code, 403)


class SearchRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client.force_login(make_user())

    def test_blocked_after_30_searches_in_a_minute(self):
        for _ in range(30):
            self.assertEqual(self.client.post(reverse("topic_search"), {"query": ""}).status_code, 400)
        self.assertEqual(self.client.post(reverse("topic_search"), {"query": ""}).status_code, 403)