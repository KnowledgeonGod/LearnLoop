from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from unittest.mock import patch

from apps.learning.services import generate_plan
from apps.resources.models import MAX_URL_LENGTH, RESOURCES_PER_STEP, Resource, StepResource
from apps.resources.services import (
    CACHE_TTL,
    KINDS_FOR_STEP,
    MAX_PER_FETCH,
    ResourceFetchFailed,
    attach_resources,
    clean_drafts,
    pick_resources,
    resources_for,
)

from .helpers import (
    FailingFetcher, FixedFetcher, RecordingFetcher, SelectiveFetcher,
    draft, make_plan, make_topic, make_user,
)

VIDEO = ["video"]


class CleanDraftsTests(SimpleTestCase):  # unit tests
    def test_valid_draft_is_cleaned(self):
        result = clean_drafts([draft(title="  A   good  video ", url="  https://example.com/v  ")], VIDEO)
        self.assertEqual(result, [("video", "A good video", "https://example.com/v", "test")])

    def test_unsafe_or_malformed_urls_are_dropped(self):  # security + edge cases
        bad_urls = [
            "javascript:alert(1)", "data:text/html,x", "ftp://example.com/f", "file:///etc/passwd",
            "//evil.example.com", "http://", "HTTP://EXAMPLE.COM", "http://user:pw@example.com/",
            "https://exa mple.com", "http://[::1", "example.com", "",
        ]
        for url in bad_urls:
            with self.subTest(url=url):
                self.assertEqual(clean_drafts([draft(url=url)], VIDEO), [])

    def test_bad_titles_are_dropped(self):
        for title in ("", "   ", None, 5, "x" * 201):
            with self.subTest(title=title):
                self.assertEqual(clean_drafts([draft(title=title)], VIDEO), [])

    def test_wrong_or_unknown_kind_is_dropped(self):
        for kind in ("book", "nonsense", None):
            with self.subTest(kind=kind):
                self.assertEqual(clean_drafts([draft(kind=kind)], VIDEO), [])

    def test_bad_sources_are_dropped(self):
        for source in ("", "x" * 31, None):
            with self.subTest(source=source):
                self.assertEqual(clean_drafts([draft(source=source)], VIDEO), [])

    def test_url_length_boundary(self):
        base = "https://example.com/"
        self.assertEqual(len(clean_drafts([draft(url=base + "a" * (MAX_URL_LENGTH - len(base)))], VIDEO)), 1)
        self.assertEqual(clean_drafts([draft(url=base + "a" * (MAX_URL_LENGTH - len(base) + 1))], VIDEO), [])

    def test_duplicate_urls_keep_the_first(self):
        result = clean_drafts([draft(title="First"), draft(title="Second")], VIDEO)
        self.assertEqual([r[1] for r in result], ["First"])

    def test_cap_on_how_many_are_kept(self):
        drafts = [draft(url=f"https://example.com/{i}") for i in range(MAX_PER_FETCH + 5)]
        self.assertEqual(len(clean_drafts(drafts, VIDEO)), MAX_PER_FETCH)

    def test_junk_is_dropped_but_good_items_are_kept(self):
        drafts = [draft(url="javascript:x"), draft(title="ok", url="https://example.com/ok")]
        self.assertEqual(len(clean_drafts(drafts, VIDEO)), 1)

    def test_garbage_input_gives_nothing(self):
        self.assertEqual(clean_drafts(None, VIDEO), [])
        self.assertEqual(clean_drafts([object()], VIDEO), [])


class PickResourcesTests(SimpleTestCase):
    items = ["a", "b", "c", "d"]

    def test_empty_gives_empty(self):
        self.assertEqual(pick_resources([], 0), [])

    def test_single_resource_is_returned_every_time(self):
        for occurrence in range(3):
            self.assertEqual(pick_resources(["a"], occurrence), ["a"])

    def test_each_occurrence_gets_the_next_slice_then_wraps(self):
        self.assertEqual(pick_resources(self.items, 0), ["a", "b"])
        self.assertEqual(pick_resources(self.items, 1), ["c", "d"])
        self.assertEqual(pick_resources(self.items, 2), ["a", "b"])

    def test_odd_length_wraps_around(self):
        self.assertEqual(pick_resources(["a", "b", "c"], 1), ["c", "a"])

    def test_never_repeats_within_one_step(self):
        for count in range(1, 6):
            for occurrence in range(10):
                picked = pick_resources(list("abcde")[:count], occurrence)
                self.assertEqual(len(picked), len(set(picked)))


class ResourcesForTests(TestCase):
    @classmethod
    def setUpTestData(cls):  # fixture
        cls.topic = make_topic()

    def test_fetches_and_saves(self):
        result = resources_for(topic=self.topic, step_type="watch")
        self.assertEqual(len(result), 4)
        self.assertEqual({r.kind for r in result}, {"video"})
        self.assertEqual({r.source for r in result}, {"fake"})
        self.assertEqual(Resource.objects.count(), 4)

    def test_second_call_uses_the_cache(self):
        fetcher = RecordingFetcher()
        first = resources_for(topic=self.topic, step_type="watch", fetcher=fetcher)
        second = resources_for(topic=self.topic, step_type="watch", fetcher=fetcher)
        self.assertEqual(len(fetcher.calls), 1)
        self.assertEqual([r.pk for r in first], [r.pk for r in second])

    def test_cache_hit_works_even_if_the_source_is_now_down(self):
        resources_for(topic=self.topic, step_type="watch")
        result = resources_for(topic=self.topic, step_type="watch", fetcher=FailingFetcher())
        self.assertEqual(len(result), 4)

    def test_stale_cache_is_refetched_without_duplicating_rows(self):
        resources_for(topic=self.topic, step_type="watch")
        Resource.objects.update(fetched_at=timezone.now() - CACHE_TTL - timedelta(days=1))
        fetcher = RecordingFetcher()
        result = resources_for(topic=self.topic, step_type="watch", fetcher=fetcher)
        self.assertEqual(len(fetcher.calls), 1)
        self.assertEqual(Resource.objects.count(), 4)
        recent = timezone.now() - timedelta(minutes=1)
        self.assertTrue(all(r.fetched_at > recent for r in result))

    def test_each_step_type_is_cached_separately(self):
        resources_for(topic=self.topic, step_type="watch")
        fetcher = RecordingFetcher()
        resources_for(topic=self.topic, step_type="read", fetcher=fetcher)
        self.assertEqual(len(fetcher.calls), 1)

    def test_each_topic_is_cached_separately(self):
        resources_for(topic=self.topic, step_type="watch")
        resources_for(topic=make_topic("Rust"), step_type="watch")
        self.assertEqual(Resource.objects.count(), 8)

    def test_source_failure_raises_and_saves_nothing(self):
        with self.assertRaises(ResourceFetchFailed):
            resources_for(topic=self.topic, step_type="watch", fetcher=FailingFetcher())
        self.assertEqual(Resource.objects.count(), 0)

    def test_all_junk_raises_and_saves_nothing(self):
        fetcher = FixedFetcher([draft(url="javascript:alert(1)")])
        with self.assertRaises(ResourceFetchFailed):
            resources_for(topic=self.topic, step_type="watch", fetcher=fetcher)
        self.assertEqual(Resource.objects.count(), 0)

    def test_junk_is_skipped_and_good_items_saved(self):
        fetcher = FixedFetcher([draft(url="javascript:x"), draft(url="https://example.com/ok")])
        result = resources_for(topic=self.topic, step_type="watch", fetcher=fetcher)
        self.assertEqual([r.url for r in result], ["https://example.com/ok"])

    def test_unknown_step_type_raises(self):
        with self.assertRaises(ValueError):
            resources_for(topic=self.topic, step_type="listen")

    @override_settings(RESOURCE_BACKEND="nope")
    def test_unknown_backend_is_reported_as_a_fetch_failure(self):
        with self.assertRaises(ResourceFetchFailed):
            resources_for(topic=self.topic, step_type="watch")


class ConstraintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.topic = make_topic()
        cls.plan = make_plan(make_user(), cls.topic)

    def make_resource(self, **overrides):
        values = dict(topic=self.topic, kind="video", title="T", url="https://example.com/a", source="x")
        return Resource.objects.create(**{**values, **overrides})

    def test_database_rejects_bad_resources(self):
        bad = [
            {"kind": "nonsense"}, {"title": ""},
            {"url": "javascript:alert(1)"}, {"url": "ftp://example.com/a"}, {"url": "data:text/html,x"},
        ]
        for overrides in bad:
            with self.subTest(overrides=overrides), self.assertRaises(IntegrityError), transaction.atomic():
                self.make_resource(**overrides)

    def test_database_rejects_duplicate_topic_url(self):
        self.make_resource()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_resource()

    def test_same_url_is_allowed_on_a_different_topic(self):
        self.make_resource()
        self.make_resource(topic=make_topic("Rust"))
        self.assertEqual(Resource.objects.count(), 2)

    def test_database_rejects_duplicate_links_and_positions(self):
        step = self.plan.steps.first()
        first, second = self.make_resource(), self.make_resource(url="https://example.com/b")
        StepResource.objects.create(step=step, resource=first, position=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            StepResource.objects.create(step=step, resource=first, position=2)  # same resource
        with self.assertRaises(IntegrityError), transaction.atomic():
            StepResource.objects.create(step=step, resource=second, position=1)  # same position

    def test_database_rejects_position_out_of_range(self):
        step, resource = self.plan.steps.first(), self.make_resource()
        for position in (0, RESOURCES_PER_STEP + 1):
            with self.subTest(position=position), self.assertRaises(IntegrityError), transaction.atomic():
                StepResource.objects.create(step=step, resource=resource, position=position)


class AttachResourcesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.topic = make_topic()
        cls.plan = make_plan(cls.user, cls.topic)

    def links_for(self, step):
        return list(StepResource.objects.filter(step=step).select_related("resource"))

    def test_every_step_gets_matching_resources(self):
        steps = list(self.plan.steps.all())
        self.assertEqual(attach_resources(plan=self.plan), len(steps))
        for step in steps:
            links = self.links_for(step)
            self.assertTrue(1 <= len(links) <= RESOURCES_PER_STEP)
            self.assertEqual([l.position for l in links], list(range(1, len(links) + 1)))
            for link in links:
                self.assertIn(link.resource.kind, KINDS_FOR_STEP[step.step_type])

    def test_fetches_once_per_step_type_not_once_per_step(self):
        fetcher = RecordingFetcher()
        attach_resources(plan=self.plan, fetcher=fetcher)
        self.assertEqual(sorted(q.step_type for q in fetcher.calls), ["practice", "read", "watch"])

    def test_steps_of_the_same_type_get_different_resources(self):
        attach_resources(plan=self.plan)
        practice = [s for s in self.plan.steps.all() if s.step_type == "practice"]
        first = {l.resource_id for l in self.links_for(practice[0])}
        second = {l.resource_id for l in self.links_for(practice[1])}
        self.assertFalse(first & second)

    def test_attaching_twice_gives_the_same_result(self):  # idempotent
        attach_resources(plan=self.plan)
        before = StepResource.objects.count()
        attach_resources(plan=self.plan)
        self.assertEqual(StepResource.objects.count(), before)

    def test_rebuilding_the_plan_keeps_the_shared_resources(self):
        attach_resources(plan=self.plan)
        resource_count = Resource.objects.count()
        rebuilt = generate_plan(user=self.user, topic=self.topic)  # deletes and recreates the steps
        self.assertEqual(StepResource.objects.count(), 0)  # old links went with the old steps
        attach_resources(plan=rebuilt)
        self.assertEqual(Resource.objects.count(), resource_count)  # served from the cache
        self.assertEqual(StepResource.objects.exclude(step__plan=rebuilt).count(), 0)
        self.assertGreater(StepResource.objects.count(), 0)

    def test_a_second_user_reuses_the_cache(self):
        attach_resources(plan=self.plan)
        bob_plan = make_plan(make_user("bob@example.com"), self.topic)
        fetcher = RecordingFetcher()
        attach_resources(plan=bob_plan, fetcher=fetcher)
        self.assertEqual(fetcher.calls, [])

    def test_one_failing_step_type_does_not_block_the_others(self):
        with self.assertLogs("apps.resources.services", level="WARNING"):
            count = attach_resources(plan=self.plan, fetcher=SelectiveFetcher({"practice"}))
        self.assertEqual(count, 4)  # the 2 read and 2 watch steps
        for step in self.plan.steps.all():
            has_links = bool(self.links_for(step))
            self.assertEqual(has_links, step.step_type != "practice")

    def test_total_failure_attaches_nothing_and_does_not_raise(self):
        with self.assertLogs("apps.resources.services", level="WARNING"):
            count = attach_resources(plan=self.plan, fetcher=FailingFetcher())
        self.assertEqual(count, 0)
        self.assertEqual(StepResource.objects.count(), 0)

    def test_failure_while_saving_keeps_the_old_links(self):  # atomicity, via mocking
        attach_resources(plan=self.plan)
        before = StepResource.objects.count()
        with patch("apps.resources.services.StepResource.objects.bulk_create", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                attach_resources(plan=self.plan)
        self.assertEqual(StepResource.objects.count(), before)  # the delete was rolled back too