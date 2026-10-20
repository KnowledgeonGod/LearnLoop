from django.test import TestCase

from apps.resources.models import StepResource
from apps.resources.selectors import resources_by_step
from apps.resources.services import attach_resources

from .helpers import make_plan, make_topic, make_user


class ResourcesByStepTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        topic = make_topic()
        cls.alice_plan = make_plan(make_user("alice@example.com"), topic)
        cls.bob_plan = make_plan(make_user("bob@example.com"), topic)
        attach_resources(plan=cls.alice_plan)
        attach_resources(plan=cls.bob_plan)

    def test_only_this_plans_steps_are_returned(self):
        by_step = resources_by_step(self.alice_plan)
        alice_steps = {s.pk for s in self.alice_plan.steps.all()}
        self.assertTrue(set(by_step) <= alice_steps)
        self.assertEqual(set(by_step), alice_steps)

    def test_resources_come_back_in_position_order(self):
        by_step = resources_by_step(self.alice_plan)
        for step in self.alice_plan.steps.all():
            expected = [l.resource_id for l in StepResource.objects.filter(step=step).order_by("position")]
            self.assertEqual([r.pk for r in by_step[step.pk]], expected)

    def test_one_query_for_the_whole_plan(self):  # N+1 guard
        with self.assertNumQueries(1):
            resources_by_step(self.alice_plan)

    def test_plan_without_links_gives_an_empty_dict(self):
        other = make_plan(make_user("carol@example.com"), make_topic("Rust"))
        self.assertEqual(resources_by_step(other), {})