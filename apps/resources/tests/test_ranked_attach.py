from unittest.mock import Mock

from django.test import TestCase

from apps.resources.models import Resource, StepResource
from apps.resources.services import attach_resources, resources_for

from .helpers import FixedFetcher, draft, make_plan, make_topic, make_user


class RankedAttachTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.topic = make_topic()
        cls.plan = make_plan(cls.user, cls.topic)  # mix: 2 read, 2 watch, 3 practice steps

    def default_watch(self):
        return resources_for(topic=self.topic, step_type="watch")

    def first_link(self, step_type):
        step = self.plan.steps.filter(step_type=step_type).first()
        return StepResource.objects.filter(step=step).order_by("position").first().resource

    def test_without_a_ranker_the_default_order_is_used(self):
        attach_resources(plan=self.plan)
        self.assertEqual(self.first_link("watch").pk, self.default_watch()[0].pk)

    def test_a_ranker_decides_what_each_step_gets(self):
        attach_resources(plan=self.plan, rank=lambda resources: list(reversed(resources)))
        self.assertEqual(self.first_link("watch").pk, self.default_watch()[-1].pk)

    def test_a_ranker_may_drop_resources(self):
        attach_resources(plan=self.plan, rank=lambda resources: resources[:2])
        default = self.default_watch()
        shown = set(
            StepResource.objects.filter(step__plan=self.plan, resource__kind="video")
            .values_list("resource_id", flat=True)
        )
        self.assertEqual(shown, {default[0].pk, default[1].pk})

    def test_a_broken_ranker_falls_back_to_the_default_order(self):
        def broken(resources):
            raise RuntimeError("boom")

        with self.assertLogs("apps.resources.services", level="ERROR"):
            count = attach_resources(plan=self.plan, rank=broken)
        self.assertEqual(count, self.plan.steps.count())  # nobody lost their links
        self.assertEqual(self.first_link("watch").pk, self.default_watch()[0].pk)

    def test_invalid_rankers_are_ignored(self):
        foreign = Resource(
            pk=999999, topic=self.topic, kind="video", title="x", url="https://example.com/x", source="t"
        )
        bad_rankers = {
            "repeats": lambda resources: [resources[0], resources[0]],
            "invents": lambda resources: resources + [foreign],
        }
        for name, bad in bad_rankers.items():
            with self.subTest(name), self.assertLogs("apps.resources.services", level="ERROR"):
                attach_resources(plan=self.plan, rank=bad)
                self.assertEqual(self.first_link("watch").pk, self.default_watch()[0].pk)

    def test_a_ranker_may_drop_everything(self):
        # Hiding every resource is legitimate (the user disliked/skipped them all).
        count = attach_resources(plan=self.plan, rank=lambda resources: [])
        self.assertEqual(count, 0)
        self.assertEqual(StepResource.objects.filter(step__plan=self.plan).count(), 0)

    def test_the_ranker_is_called_once_per_step_type_not_per_step(self):
        rank = Mock(side_effect=lambda resources: list(resources))
        attach_resources(plan=self.plan, rank=rank)
        self.assertEqual(rank.call_count, 3)  # read, watch, practice

    def test_a_single_resource_is_still_ranked(self):
        # Even one resource must go through the ranker: this user may have hidden it.
        topic = make_topic("Rust")
        plan = make_plan(self.user, topic, preferred_format="video")  # watch steps only
        rank = Mock(side_effect=lambda resources: list(resources))
        attach_resources(plan=plan, fetcher=FixedFetcher([draft()]), rank=rank)
        self.assertTrue(rank.called)  # a single resource still goes through the ranker
        self.assertTrue(all(len(call.args[0]) == 1 for call in rank.call_args_list))
        # Only one resource exists, so only one step can get it. It is never repeated.
        links = StepResource.objects.filter(step__plan=plan)
        self.assertEqual(links.count(), 1)
        self.assertEqual(links.values("step_id").distinct().count(), 1)