from django.test import TestCase, override_settings
from django.urls import reverse

from apps.feedback.services import set_feedback
from apps.learning.personalize import ranker_for, reasons_for
from apps.learning.services import generate_plan
from apps.resources.models import Resource, StepResource
from apps.resources.services import attach_resources

from .helpers import make_attempt, make_topic, make_user


def build(user, topic, ranked=True, **answers):
    make_attempt(user, topic, **answers)
    plan = generate_plan(user=user, topic=topic)
    attach_resources(plan=plan, rank=ranker_for(user, topic) if ranked else None)
    return plan


def shown_ids(plan):
    return set(StepResource.objects.filter(step__plan=plan).values_list("resource_id", flat=True))


def first_resource(plan):
    step = plan.steps.first()
    return StepResource.objects.filter(step=step).order_by("position").first().resource


class PersonalizedPlanTests(TestCase):
    @classmethod
    def setUpTestData(cls):  # fixture
        cls.alice = make_user("alice@example.com")
        cls.bob = make_user("bob@example.com")
        cls.carol = make_user("carol@example.com")
        cls.topic = make_topic()
        # Bob's unranked plan creates the shared resources: 4 videos, saved in this order.
        cls.bob_plan = build(cls.bob, cls.topic, ranked=False, level="some")
        cls.videos = list(Resource.objects.filter(topic=cls.topic, kind="video").order_by("pk"))

    def test_without_any_signal_the_default_order_holds(self):
        plan = build(self.alice, self.topic, level="some")
        self.assertEqual(first_resource(plan).pk, self.videos[0].pk)

    def test_another_learners_like_does_not_change_my_order(self):
        set_feedback(user=self.bob, resource=self.videos[3], action="like")
        set_feedback(user=self.carol, resource=self.videos[3], action="like")
        plan = build(self.alice, self.topic, level="some")
        self.assertEqual(first_resource(plan).pk, self.videos[0].pk)

    def test_another_learners_dislike_does_not_hide_a_resource_from_me(self):
        set_feedback(user=self.bob, resource=self.videos[0], action="dislike")
        set_feedback(user=self.carol, resource=self.videos[0], action="dislike")
        self.assertIn(self.videos[0].pk, shown_ids(build(self.alice, self.topic, level="some")))

    def test_a_dislike_removes_it_from_my_next_plan_but_not_from_others(self):
        first = first_resource(build(self.alice, self.topic, level="some"))
        set_feedback(user=self.alice, resource=first, action="dislike")
        rebuilt = build(self.alice, self.topic, level="some")
        self.assertNotIn(first.pk, shown_ids(rebuilt))
        self.assertIn(first.pk, shown_ids(self.bob_plan))

    def test_a_skip_also_removes_it(self):
        first = first_resource(build(self.alice, self.topic, level="some"))
        set_feedback(user=self.alice, resource=first, action="skip")
        self.assertNotIn(first.pk, shown_ids(build(self.alice, self.topic, level="some")))

    def test_no_resource_appears_twice_in_one_plan(self):
        plan = build(self.alice, self.topic, level="some")
        shown = list(
            StepResource.objects.filter(step__plan=plan).values_list("resource_id", flat=True)
        )
        self.assertEqual(len(shown), len(set(shown)))

    def test_rebuilding_after_a_dislike_never_repeats_a_link(self):
        plan = build(self.alice, self.topic, level="some")
        disliked = first_resource(plan)
        set_feedback(user=self.alice, resource=disliked, action="dislike")
        rebuilt = build(self.alice, self.topic, level="some")
        shown = list(
            StepResource.objects.filter(step__plan=rebuilt).values_list("resource_id", flat=True)
        )
        self.assertEqual(len(shown), len(set(shown)))
        self.assertNotIn(disliked.pk, shown)

    def test_disliking_everything_never_brings_a_hidden_resource_back(self):  # edge case
        for video in self.videos:
            set_feedback(user=self.alice, resource=video, action="dislike")
        plan = build(self.alice, self.topic, level="some")
        shown = shown_ids(plan)
        self.assertFalse(shown & {video.pk for video in self.videos})

    def test_level_shapes_the_order(self):
        beginner = first_resource(build(self.alice, self.topic, level="beginner"))
        self.assertIn("for beginners", beginner.title)
        intermediate = first_resource(build(self.alice, self.topic, level="intermediate"))
        self.assertIn("full course", intermediate.title)

    def test_a_ranker_for_someone_without_a_quiz_is_neutral(self):
        ordered = ranker_for(self.carol, self.topic)(list(self.videos))
        self.assertEqual([r.pk for r in ordered], [v.pk for v in self.videos])


class ReasonsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user("alice@example.com")
        cls.bob = make_user("bob@example.com")
        cls.carol = make_user("carol@example.com")
        cls.topic = make_topic()
        build(cls.bob, cls.topic, ranked=False, level="some")
        cls.videos = list(Resource.objects.filter(topic=cls.topic, kind="video").order_by("pk"))

    def test_another_learners_feedback_adds_no_reason(self):
        make_attempt(self.alice, self.topic, level="some")
        set_feedback(user=self.bob, resource=self.videos[3], action="like")
        set_feedback(user=self.carol, resource=self.videos[3], action="like")
        result = reasons_for(self.alice, self.topic, self.videos)
        self.assertEqual(result[self.videos[3].pk], [])
        self.assertEqual(result[self.videos[0].pk], [])

    def test_my_own_like_is_explained(self):
        make_attempt(self.alice, self.topic, level="some")
        set_feedback(user=self.alice, resource=self.videos[0], action="like")
        self.assertEqual(reasons_for(self.alice, self.topic, self.videos)[self.videos[0].pk], ["You liked this"])

    def test_level_match_is_explained(self):
        make_attempt(self.alice, self.topic, level="beginner")
        result = reasons_for(self.alice, self.topic, self.videos)
        self.assertEqual(result[self.videos[0].pk], ["Matches your level"])  # "for beginners"
        self.assertEqual(result[self.videos[1].pk], [])  # "full course"

    def test_two_queries_however_many_resources(self):  # N+1 guard
        make_attempt(self.alice, self.topic, level="some")
        with self.assertNumQueries(2):
            reasons_for(self.alice, self.topic, self.videos)

    def test_an_empty_plan_makes_no_query(self):
        with self.assertNumQueries(0):
            self.assertEqual(reasons_for(self.alice, self.topic, []), {})


@override_settings(RATELIMIT_ENABLE=False)
class PlanPageTests(TestCase):  # integration tests
    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user("alice@example.com")
        cls.bob = make_user("bob@example.com")
        cls.carol = make_user("carol@example.com")
        cls.topic = make_topic()
        build(cls.bob, cls.topic, ranked=False, level="some")
        cls.videos = list(Resource.objects.filter(topic=cls.topic, kind="video").order_by("pk"))
        cls.plan_url = reverse("plan", args=[cls.topic.pk])
        cls.generate_url = reverse("plan_generate", args=[cls.topic.pk])

    def setUp(self):
        self.client.force_login(self.alice)
        make_attempt(self.alice, self.topic, level="some")

    def test_another_users_feedback_never_changes_my_page(self):
        set_feedback(user=self.bob, resource=self.videos[3], action="like")
        set_feedback(user=self.carol, resource=self.videos[3], action="like")
        self.client.post(self.generate_url)
        resp = self.client.get(self.plan_url)
        self.assertNotContains(resp, "Liked by other learners")

    def test_my_own_like_is_explained_on_the_page(self):
        set_feedback(user=self.alice, resource=self.videos[0], action="like")
        self.client.post(self.generate_url)
        self.assertContains(self.client.get(self.plan_url), "You liked this")

    def test_a_dislike_through_the_page_removes_the_resource_after_a_rebuild(self):
        self.client.post(self.generate_url)
        link = (
            StepResource.objects.filter(step__plan__user=self.alice)
            .select_related("resource")
            .order_by("step__position", "position")
            .first()
        )
        disliked = link.resource
        self.assertContains(self.client.get(self.plan_url), disliked.title)
        self.client.post(reverse("resource_feedback", args=[disliked.pk]), {"action": "dislike"})
        self.client.post(self.generate_url)

        shown = set(
            StepResource.objects.filter(step__plan__user=self.alice)
            .values_list("resource_id", flat=True)
        )
        self.assertNotIn(disliked.pk, shown)  # gone from the plan itself
        resp = self.client.get(self.plan_url)
        self.assertContains(resp, "Hidden resources (1)")  # but still undoable
        self.assertContains(resp, disliked.title)