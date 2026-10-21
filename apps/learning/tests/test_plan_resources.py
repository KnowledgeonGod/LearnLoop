from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.feedback.services import set_feedback
from apps.learning.models import LearningPlan
from apps.resources.models import Resource, StepResource

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
        self.assertNotContains(resp, "No resources are left")

    def test_attach_failure_does_not_break_plan_generation(self):
        with patch("apps.learning.views.attach_resources", side_effect=RuntimeError):
            with self.assertLogs("apps.learning.views", level="ERROR"):
                resp = self.client.post(self.generate_url)
        self.assertRedirects(resp, self.plan_url)
        self.assertEqual(LearningPlan.objects.filter(user=self.alice).count(), 1)
        self.assertContains(
            self.client.get(self.plan_url), "No resources are available for this plan right now"
        )

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

    @patch("apps.resources.services.MAX_REFILL_ROUNDS", 0)
    def test_a_section_whose_resources_are_all_hidden_is_removed(self):
        make_attempt(self.alice, self.topic, preferred_format="mix")
        self.client.post(self.generate_url)
        for video in Resource.objects.filter(topic=self.topic, kind="video"):
            set_feedback(user=self.alice, resource=video, action="dislike")
        self.client.post(self.generate_url)

        plan = LearningPlan.objects.get(user=self.alice, topic=self.topic)
        resp = self.client.get(self.plan_url)
        watch_steps = plan.steps.filter(step_type="watch")
        self.assertTrue(watch_steps.exists())
        for step in watch_steps:
            self.assertNotContains(resp, step.title)
        # The other sections are untouched.
        self.assertContains(resp, "Guided practice")
        self.assertContains(resp, "Foundations of Python")

    def test_refill_keeps_a_section_alive_after_page_zero_is_hidden(self):
        make_attempt(self.alice, self.topic, preferred_format="mix")
        self.client.post(self.generate_url)
        page_zero = list(Resource.objects.filter(topic=self.topic, kind="video"))
        self.assertTrue(page_zero)
        for video in page_zero:
            set_feedback(user=self.alice, resource=video, action="dislike")
        self.client.post(self.generate_url)

        resp = self.client.get(self.plan_url)
        self.assertContains(resp, "Core ideas of Python")  # refilled, not removed
        self.assertContains(resp, "part 2")  # a genuinely new page of resources
        for video in page_zero:
            self.assertNotContains(resp, f'href="{video.url}"')

    @patch("apps.resources.services.MAX_REFILL_ROUNDS", 0)
    def test_removing_every_section_shows_a_clear_message(self):
        self.client.post(self.generate_url)
        for resource in Resource.objects.filter(topic=self.topic):
            set_feedback(user=self.alice, resource=resource, action="dislike")
        self.client.post(self.generate_url)

        resp = self.client.get(self.plan_url)
        self.assertContains(resp, "No resources are left for this plan")
        self.assertNotContains(resp, "Core ideas of Python")
        self.assertNotContains(resp, "Guided practice")

    def test_a_dislike_hides_the_resource_immediately_even_before_a_rebuild(self):
        self.client.post(self.generate_url)
        resource = (
            StepResource.objects.filter(step__plan__user=self.alice)
            .select_related("resource")
            .first()
            .resource
        )
        self.assertContains(self.client.get(self.plan_url), f'href="{resource.url}"')
        self.client.post(reverse("resource_feedback", args=[resource.pk]), {"action": "dislike"})
        resp = self.client.get(self.plan_url)
        self.assertNotContains(resp, f'href="{resource.url}"')
        self.assertContains(resp, "Hidden resources (1)")

    @patch("apps.resources.services.MAX_REFILL_ROUNDS", 0)
    def test_undoing_the_hidden_feedback_brings_the_section_back_after_a_rebuild(self):
        make_attempt(self.alice, self.topic, preferred_format="mix")
        self.client.post(self.generate_url)
        videos = Resource.objects.filter(topic=self.topic, kind="video")
        for video in videos:
            set_feedback(user=self.alice, resource=video, action="dislike")
        self.client.post(self.generate_url)
        self.assertNotContains(self.client.get(self.plan_url), "Core ideas of Python")

        for video in videos:
            set_feedback(user=self.alice, resource=video, action="clear_rating")
        self.client.post(self.generate_url)
        self.assertContains(self.client.get(self.plan_url), "Core ideas of Python")

    def test_hidden_resources_are_listed_outside_the_plan_with_an_undo(self):
        self.client.post(self.generate_url)
        resource = (
            StepResource.objects.filter(step__plan__user=self.alice)
            .select_related("resource")
            .first()
            .resource
        )
        set_feedback(user=self.alice, resource=resource, action="dislike")
        self.client.post(self.generate_url)

        resp = self.client.get(self.plan_url)
        self.assertContains(resp, "Hidden resources (1)")
        self.assertContains(resp, resource.title)
        self.assertContains(resp, "Disliked (undo)")