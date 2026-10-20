from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.quiz.models import QuizAttempt
from apps.quiz.services import submit_quiz

from .helpers import ANSWERS, make_topic, make_user

POST_DATA = {
    "level": "some",
    "goal": "job",
    "hours_per_week": "6",
    "weeks_available": "8",
    "preferred_format": "video",
    "depth": "deep",
}


@override_settings(RATELIMIT_ENABLE=False)
class QuizViewTests(TestCase):  # integration tests
    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user("alice@example.com")
        cls.bob = make_user("bob@example.com")
        cls.topic = make_topic()

    def setUp(self):
        self.client.force_login(self.alice)
        self.url = reverse("quiz", args=[self.topic.pk])

    def test_requires_login(self):
        self.client.logout()
        for resp in (self.client.get(self.url), self.client.post(self.url, POST_DATA)):
            self.assertEqual(resp.status_code, 302)
            self.assertIn(reverse("login"), resp.url)

    def test_get_shows_the_quiz(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, self.topic.name)
        self.assertEqual(resp.status_code, 200)

    def test_valid_post_saves_and_redirects_to_the_topic(self):
        resp = self.client.post(self.url, POST_DATA)
        self.assertRedirects(resp, reverse("topic_detail", args=[self.topic.pk]))
        self.assertEqual(QuizAttempt.objects.get(user=self.alice).goal, "job")

    def test_saved_answers_prefill_the_form(self):
        submit_quiz(user=self.alice, topic=self.topic, answers=ANSWERS)
        resp = self.client.get(self.url)
        self.assertEqual(resp.context["form"].initial["goal"], "job")

    def test_retaking_updates_the_same_row(self):
        self.client.post(self.url, POST_DATA)
        self.client.post(self.url, {**POST_DATA, "level": "intermediate"})
        self.assertEqual(QuizAttempt.objects.count(), 1)
        self.assertEqual(QuizAttempt.objects.get().level, "intermediate")

    def test_invalid_post_shows_errors_and_saves_nothing(self):
        resp = self.client.post(self.url, {**POST_DATA, "hours_per_week": "0"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("hours_per_week", resp.context["form"].errors)
        self.assertEqual(QuizAttempt.objects.count(), 0)

    def test_missing_answer_is_rejected(self):
        data = {k: v for k, v in POST_DATA.items() if k != "goal"}
        resp = self.client.post(self.url, data)
        self.assertIn("goal", resp.context["form"].errors)
        self.assertEqual(QuizAttempt.objects.count(), 0)

    def test_unknown_topic_is_404(self):
        self.assertEqual(self.client.get(reverse("quiz", args=[99999])).status_code, 404)

    def test_users_cannot_see_or_change_each_others_answers(self):  # IDOR
        submit_quiz(user=self.alice, topic=self.topic, answers=ANSWERS)
        self.client.force_login(self.bob)
        resp = self.client.get(self.url)
        self.assertNotEqual(resp.context["form"].initial.get("goal"), "job")  # not Alice's data
        self.client.post(self.url, {**POST_DATA, "goal": "exam"})
        self.assertEqual(QuizAttempt.objects.get(user=self.alice).goal, "job")
        self.assertEqual(QuizAttempt.objects.get(user=self.bob).goal, "exam")

    def test_post_without_csrf_token_is_rejected(self):  # CSRF
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.alice)
        self.assertEqual(client.post(self.url, POST_DATA).status_code, 403)

    def test_topic_name_is_escaped(self):  # XSS
        evil = make_topic("<b>x</b>")
        resp = self.client.get(reverse("quiz", args=[evil.pk]))
        self.assertNotContains(resp, "<b>x</b>")

    def test_topic_page_links_to_the_quiz(self):
        resp = self.client.get(reverse("topic_detail", args=[self.topic.pk]))
        self.assertContains(resp, self.url)


class QuizRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client.force_login(make_user())
        self.url = reverse("quiz", args=[make_topic().pk])

    def test_blocked_after_20_submissions_in_a_minute(self):
        for _ in range(20):
            self.assertEqual(self.client.post(self.url, {}).status_code, 200)
        self.assertEqual(self.client.post(self.url, {}).status_code, 403)