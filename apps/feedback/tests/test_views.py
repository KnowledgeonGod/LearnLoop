from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.feedback.models import ResourceFeedback

from .helpers import a_resource_of_kind, make_plan_with_resources, make_resource, make_topic, make_user, scores


@override_settings(RATELIMIT_ENABLE=False)
class FeedbackViewTests(TestCase):  # integration tests
    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user("alice@example.com")
        cls.bob = make_user("bob@example.com")
        cls.topic = make_topic()
        cls.alice_plan = make_plan_with_resources(cls.alice, cls.topic)
        make_plan_with_resources(cls.bob, cls.topic)  # shares the same cached resources
        cls.video = a_resource_of_kind(cls.alice_plan, "video")
        cls.url = reverse("resource_feedback", args=[cls.video.pk])
        cls.plan_url = reverse("plan", args=[cls.topic.pk])

    def setUp(self):
        self.client.force_login(self.alice)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(self.url, {"action": "like"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("login"), resp.url)
        self.assertEqual(ResourceFeedback.objects.count(), 0)

    def test_like_is_saved_and_redirects_to_the_plan(self):
        resp = self.client.post(self.url, {"action": "like"})
        self.assertRedirects(resp, self.plan_url)
        self.assertEqual(ResourceFeedback.objects.get(user=self.alice).rating, "like")
        self.assertEqual(scores(self.alice)["video"], 1.0)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_unknown_or_missing_action_is_a_400(self):
        for data in ({"action": "explode"}, {"action": ""}, {}):
            with self.subTest(data=data):
                self.assertEqual(self.client.post(self.url, data).status_code, 400)
        self.assertEqual(ResourceFeedback.objects.count(), 0)

    def test_resource_not_in_my_plans_is_a_404(self):  # authorization
        loose = make_resource(self.topic, "video", "https://example.com/loose")
        resp = self.client.post(reverse("resource_feedback", args=[loose.pk]), {"action": "like"})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(ResourceFeedback.objects.count(), 0)

    def test_unknown_resource_is_a_404(self):
        resp = self.client.post(reverse("resource_feedback", args=[99999]), {"action": "like"})
        self.assertEqual(resp.status_code, 404)

    def test_feedback_is_private_to_each_user(self):  # IDOR
        self.client.post(self.url, {"action": "like"})
        self.client.force_login(self.bob)
        resp = self.client.get(self.plan_url)
        self.assertNotContains(resp, "Liked (undo)")
        self.assertEqual(ResourceFeedback.objects.filter(user=self.bob).count(), 0)
        self.assertEqual(scores(self.bob), {})

    def test_plan_page_shows_the_buttons_and_the_current_state(self):
        resp = self.client.get(self.plan_url)
        self.assertContains(resp, 'name="action"')
        self.assertContains(resp, ">Like<")
        self.client.post(self.url, {"action": "like"})
        self.assertContains(self.client.get(self.plan_url), "Liked (undo)")

    def test_undo_removes_the_feedback_and_its_effect(self):
        self.client.post(self.url, {"action": "like"})
        self.client.post(self.url, {"action": "clear_rating"})
        self.assertEqual(ResourceFeedback.objects.count(), 0)
        self.assertEqual(scores(self.alice)["video"], 0)

    def test_post_without_csrf_token_is_rejected(self):  # CSRF
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.alice)
        self.assertEqual(client.post(self.url, {"action": "like"}).status_code, 403)


class FeedbackRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client.force_login(make_user())

    def test_blocked_after_60_requests_in_a_minute(self):
        url = reverse("resource_feedback", args=[1])  # no such resource, so each is a 404
        for _ in range(60):
            self.assertEqual(self.client.post(url, {"action": "like"}).status_code, 404)
        self.assertEqual(self.client.post(url, {"action": "like"}).status_code, 403)