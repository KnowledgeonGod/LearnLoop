from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.learning.models import LearningPlan
from apps.learning.services import PlanGenerationFailed

from .helpers import later, make_attempt, make_topic, make_user


@override_settings(RATELIMIT_ENABLE=False)
class PlanViewTests(TestCase):  # integration tests
    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user("alice@example.com")
        cls.bob = make_user("bob@example.com")
        cls.topic = make_topic()
        cls.plan_url = reverse("plan", args=[cls.topic.pk])
        cls.generate_url = reverse("plan_generate", args=[cls.topic.pk])

    def setUp(self):
        self.client.force_login(self.alice)

    def test_pages_require_login(self):
        self.client.logout()
        for resp in (self.client.get(self.plan_url), self.client.post(self.generate_url)):
            self.assertEqual(resp.status_code, 302)
            self.assertIn(reverse("login"), resp.url)

    def test_page_without_quiz_points_to_the_quiz(self):
        resp = self.client.get(self.plan_url)
        self.assertContains(resp, reverse("quiz", args=[self.topic.pk]))
        self.assertNotContains(resp, "Create my plan")

    def test_page_after_quiz_offers_to_create_the_plan(self):
        make_attempt(self.alice, self.topic)
        self.assertContains(self.client.get(self.plan_url), "Create my plan")

    def test_generate_builds_the_plan_and_shows_it(self):
        make_attempt(self.alice, self.topic)
        resp = self.client.post(self.generate_url)
        self.assertRedirects(resp, self.plan_url)
        self.assertEqual(LearningPlan.objects.filter(user=self.alice).count(), 1)
        self.assertContains(self.client.get(self.plan_url), "Core ideas of Python")

    def test_generate_without_quiz_redirects_to_the_quiz(self):
        resp = self.client.post(self.generate_url)
        self.assertRedirects(resp, reverse("quiz", args=[self.topic.pk]))
        self.assertEqual(LearningPlan.objects.count(), 0)

    def test_generate_is_post_only(self):
        self.assertEqual(self.client.get(self.generate_url).status_code, 405)

    def test_stale_plan_shows_a_notice_and_a_rebuild_button(self):
        make_attempt(self.alice, self.topic)
        self.client.post(self.generate_url)
        with patch("apps.quiz.services.timezone.now", return_value=later()):
            make_attempt(self.alice, self.topic, level="intermediate")
        resp = self.client.get(self.plan_url)
        self.assertContains(resp, "answers changed")
        self.assertContains(resp, "Rebuild my plan")

    def test_failure_shows_a_generic_message_not_the_internal_reason(self):
        make_attempt(self.alice, self.topic)
        error = PlanGenerationFailed("secret internal detail")
        with patch("apps.learning.views.generate_plan", side_effect=error):
            with self.assertLogs("apps.learning.views", level="WARNING"):
                resp = self.client.post(self.generate_url)
        self.assertContains(resp, "try again", status_code=503)
        self.assertNotContains(resp, "secret internal detail", status_code=503)

    def test_users_cannot_see_each_others_plans(self):  # IDOR
        make_attempt(self.alice, self.topic)
        self.client.post(self.generate_url)
        self.client.force_login(self.bob)
        resp = self.client.get(self.plan_url)
        self.assertNotContains(resp, "Core ideas of Python")
        self.assertContains(resp, reverse("quiz", args=[self.topic.pk]))

    def test_unknown_topic_is_404(self):
        self.assertEqual(self.client.get(reverse("plan", args=[99999])).status_code, 404)
        self.assertEqual(self.client.post(reverse("plan_generate", args=[99999])).status_code, 404)

    def test_generate_without_csrf_token_is_rejected(self):  # CSRF
        make_attempt(self.alice, self.topic)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.alice)
        self.assertEqual(client.post(self.generate_url).status_code, 403)

    def test_topic_names_are_escaped_in_the_plan(self):  # XSS
        evil = make_topic("<b>x</b>")
        make_attempt(self.alice, evil)
        self.client.post(reverse("plan_generate", args=[evil.pk]))
        resp = self.client.get(reverse("plan", args=[evil.pk]))
        self.assertNotContains(resp, "<b>x</b>")
        self.assertContains(resp, "&lt;b&gt;x&lt;/b&gt;")

    def test_topic_page_links_to_the_plan(self):
        resp = self.client.get(reverse("topic_detail", args=[self.topic.pk]))
        self.assertContains(resp, self.plan_url)


class GenerateRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client.force_login(make_user())
        self.url = reverse("plan_generate", args=[make_topic().pk])

    def test_blocked_after_10_attempts_in_an_hour(self):
        for _ in range(10):
            self.assertEqual(self.client.post(self.url).status_code, 302)  # no quiz: redirected
        self.assertEqual(self.client.post(self.url).status_code, 403)