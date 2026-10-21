from unittest.mock import patch
from apps.resources.models import StepResource

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.learning.models import LearningPlan

from .helpers import make_attempt, make_topic, make_user


@override_settings(RATELIMIT_ENABLE=False)
class PlanResourcesViewTests(TestCase):  # integration tests
    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user("alice@example.com")
        cls.bob = make_user("bob@example.com")
        cls.topic = make_topic()
        cls.plan_url = reverse("plan", args=[cls.topic.pk])
        cls.generate_url = reverse("plan_generate", args=[cls.topic.pk])

    def setUp(self):
        self.client.force_login(self.alice)
        make_attempt(self.alice, self.topic)

    def test_generated_plan_shows_safe_resource_links(self):
        self.client.post(self.generate_url)
        resp = self.client.get(self.plan_url)
        self.assertContains(resp, "youtube.com/results")
        self.assertContains(resp, 'rel="noopener noreferrer"')
        self.assertNotContains(resp, "could not be loaded")

    def test_attach_failure_does_not_break_plan_generation(self):
        with patch("apps.learning.views.attach_resources", side_effect=RuntimeError):
            with self.assertLogs("apps.learning.views", level="ERROR"):
                resp = self.client.post(self.generate_url)
        self.assertRedirects(resp, self.plan_url)
        self.assertEqual(LearningPlan.objects.filter(user=self.alice).count(), 1)
        self.assertContains(self.client.get(self.plan_url), "Some resources could not be loaded")

    def test_other_users_do_not_see_my_plan_or_its_resources(self):  # IDOR
        self.client.post(self.generate_url)
        self.client.force_login(self.bob)
        self.assertNotContains(self.client.get(self.plan_url), "youtube.com/results")

    def test_resource_titles_are_escaped(self):  # XSS
        evil = make_topic("<b>x</b>")
        make_attempt(self.alice, evil)
        self.client.post(reverse("plan_generate", args=[evil.pk]))
        resp = self.client.get(reverse("plan", args=[evil.pk]))
        self.assertNotContains(resp, "<b>x</b>")
        self.assertContains(resp, "&lt;b&gt;x&lt;/b&gt;")
        
    def test_chosen_format_decides_the_kind_of_resources(self):
        expected = {
            "video": {"video"},
            "reading": {"book", "article"},
            "practice": {"exercise"},
        }
        for fmt, kinds in expected.items():
            with self.subTest(fmt=fmt):
                make_attempt(self.alice, self.topic, preferred_format=fmt)
                self.client.post(self.generate_url)
                found = set(
                    StepResource.objects.filter(
                        step__plan__user=self.alice, step__plan__topic=self.topic
                    ).values_list("resource__kind", flat=True)
                )
                self.assertTrue(found)
                self.assertTrue(found <= kinds, f"{fmt} plan contained {found}")