from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from unittest.mock import patch

from apps.learning.services import generate_plan
from apps.resources.models import (
    MAX_URL_LENGTH,
    RESOURCES_PER_STEP,
    Resource,
    ResourceFetch,
    StepResource,
)
from apps.resources.services import (
    CACHE_TTL,
    KINDS_FOR_STEP,
    MAX_FETCH_PAGES,
    MAX_PER_FETCH,
    ResourceFetchFailed,
    attach_resources,
    clean_drafts,
    refill_resources,
    resources_for,
    split_resources,
)

from .helpers import (
    FailingFetcher, FixedFetcher, RecordingFetcher, SelectiveFetcher,
    draft, make_plan, make_topic, make_user,
)
from services.fetchers import FakeFetcher, FetchError

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


class SplitResourcesTests(SimpleTestCase):
    items = ["a", "b", "c", "d"]

    def test_empty_supply_gives_empty_slices(self):
        self.assertEqual(split_resources([], 3), [[], [], []])

    def test_no_steps_gives_no_slices(self):
        self.assertEqual(split_resources(self.items, 0), [])

    def test_two_steps_get_two_each(self):
        self.assertEqual(split_resources(self.items, 2), [["a", "b"], ["c", "d"]])

    def test_three_steps_get_an_even_share(self):
        self.assertEqual(split_resources(self.items, 3), [["a", "b"], ["c"], ["d"]])

    def test_a_short_supply_is_spread_not_repeated(self):
        self.assertEqual(split_resources(self.items, 4), [["a"], ["b"], ["c"], ["d"]])
        self.assertEqual(split_resources(["a"], 3), [["a"], [], []])

    def test_at_most_two_per_step(self):
        many = [str(i) for i in range(10)]
        self.assertEqual([len(slice_) for slice_ in split_resources(many, 3)], [2, 2, 2])

    def test_never_repeats_anywhere(self):
        for count in range(1, 8):
            for steps in range(1, 6):
                with self.subTest(count=count, steps=steps):
                    slices = split_resources(list("abcdefg")[:count], steps)
                    picked = [item for slice_ in slices for item in slice_]
                    self.assertEqual(len(picked), len(set(picked)))
                    self.assertEqual(len(picked), min(count, steps * RESOURCES_PER_STEP))
                    self.assertTrue(all(len(slice_) <= RESOURCES_PER_STEP for slice_ in slices))


class ResourcesForTests(TestCase):
    @classmethod
    def setUpTestData(cls):  # fixture
        cls.topic = make_topic()

    def test_fetches_and_saves(self):
        result = resources_for(topic=self.topic, step_type="watch")
        self.assertEqual(len(result), 8)
        self.assertEqual({r.kind for r in result}, {"video"})
        self.assertEqual({r.source for r in result}, {"fake"})
        self.assertEqual(Resource.objects.count(), 8)

    def test_second_call_uses_the_cache(self):
        fetcher = RecordingFetcher()
        first = resources_for(topic=self.topic, step_type="watch", fetcher=fetcher)
        second = resources_for(topic=self.topic, step_type="watch", fetcher=fetcher)
        self.assertEqual(len(fetcher.calls), 1)
        self.assertEqual([r.pk for r in first], [r.pk for r in second])

    def test_cache_hit_works_even_if_the_source_is_now_down(self):
        resources_for(topic=self.topic, step_type="watch")
        result = resources_for(topic=self.topic, step_type="watch", fetcher=FailingFetcher())
        self.assertEqual(len(result), 8)

    def test_stale_cache_is_refetched_without_duplicating_rows(self):
        resources_for(topic=self.topic, step_type="watch")
        Resource.objects.update(fetched_at=timezone.now() - CACHE_TTL - timedelta(days=1))
        fetcher = RecordingFetcher()
        result = resources_for(topic=self.topic, step_type="watch", fetcher=fetcher)
        self.assertEqual(len(fetcher.calls), 1)
        self.assertEqual(Resource.objects.count(), 8)
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
        self.assertEqual(Resource.objects.count(), 16)

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

    def test_refill_fetches_the_next_page_without_repeats(self):
        first = resources_for(topic=self.topic, step_type="watch")
        fetcher = RecordingFetcher()
        more = refill_resources(topic=self.topic, step_type="watch", fetcher=fetcher)
        self.assertEqual(len(fetcher.calls), 1)
        self.assertEqual(fetcher.calls[0].page, 1)
        self.assertEqual(len(more), 8)
        self.assertFalse({r.pk for r in first} & {r.pk for r in more})
        self.assertEqual(Resource.objects.count(), 16)
        self.assertEqual(
            set(ResourceFetch.objects.values_list("page", flat=True)), {0, 1}
        )

    def test_refill_stops_at_the_page_cap(self):
        resources_for(topic=self.topic, step_type="watch")
        ResourceFetch.objects.filter(topic=self.topic, step_type="watch").delete()
        ResourceFetch.objects.bulk_create(
            [
                ResourceFetch(topic=self.topic, step_type="watch", page=page)
                for page in range(MAX_FETCH_PAGES)
            ]
        )
        fetcher = RecordingFetcher()
        self.assertEqual(
            refill_resources(topic=self.topic, step_type="watch", fetcher=fetcher), []
        )
        self.assertEqual(fetcher.calls, [])

    def test_a_stale_cache_restarts_from_page_zero(self):
        resources_for(topic=self.topic, step_type="watch")
        refill_resources(topic=self.topic, step_type="watch")
        self.assertEqual(Resource.objects.count(), 16)
        Resource.objects.update(fetched_at=timezone.now() - CACHE_TTL - timedelta(days=1))

        fetcher = RecordingFetcher()
        result = resources_for(topic=self.topic, step_type="watch", fetcher=fetcher)
        self.assertEqual(len(fetcher.calls), 1)
        self.assertEqual(fetcher.calls[0].page, 0)
        self.assertEqual(len(result), 8)
        self.assertEqual(Resource.objects.count(), 16)
        self.assertEqual(
            list(ResourceFetch.objects.values_list("page", flat=True)), [0]
        )

    def test_refill_restarts_the_cache_when_nothing_is_fresh(self):
        fetcher = RecordingFetcher()
        result = refill_resources(topic=self.topic, step_type="watch", fetcher=fetcher)
        self.assertEqual(len(result), 8)
        self.assertEqual(fetcher.calls[0].page, 0)

    def test_refill_unknown_step_type_raises(self):
        with self.assertRaises(ValueError):
            refill_resources(topic=self.topic, step_type="listen")


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

    def test_fetch_pages_are_unique_per_topic_and_step_type(self):
        ResourceFetch.objects.create(topic=self.topic, step_type="watch", page=0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ResourceFetch.objects.create(topic=self.topic, step_type="watch", page=0)
        ResourceFetch.objects.create(topic=self.topic, step_type="read", page=0)
        ResourceFetch.objects.create(topic=make_topic("Rust"), step_type="watch", page=0)

    def test_database_rejects_an_unknown_fetch_step_type(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ResourceFetch.objects.create(topic=self.topic, step_type="listen", page=0)

    def test_string_representations_are_useful(self):
        resource = self.make_resource()
        self.assertEqual(str(resource), "T")
        fetched = ResourceFetch.objects.create(topic=self.topic, step_type="watch", page=0)
        self.assertEqual(str(fetched), f"{self.topic} / watch / page 0")
        step = self.plan.steps.first()
        link = StepResource.objects.create(step=step, resource=resource, position=1)
        self.assertEqual(str(link), f"{step.pk}: {resource.pk}")


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

    def test_no_resource_is_repeated_anywhere_in_the_plan(self):
        attach_resources(plan=self.plan)
        shown = list(
            StepResource.objects.filter(step__plan=self.plan).values_list("resource_id", flat=True)
        )
        self.assertEqual(len(shown), len(set(shown)))

    def test_a_short_supply_leaves_steps_empty_instead_of_repeating(self):
        # The ranker keeps one resource per step type, so only the first step of each
        # type can get a link; the others stay empty rather than reuse it.
        attach_resources(plan=self.plan, rank=lambda resources: resources[:1])
        shown = list(
            StepResource.objects.filter(step__plan=self.plan).values_list("resource_id", flat=True)
        )
        self.assertEqual(len(shown), len(set(shown)))
        self.assertEqual(len(shown), 3)  # one per step type
        self.assertEqual(
            StepResource.objects.filter(step__plan=self.plan).values("step_id").distinct().count(), 3
        )

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

    def test_attach_refills_when_ranking_leaves_a_gap(self):
        fetcher = RecordingFetcher()
        attach_resources(
            plan=self.plan,
            fetcher=fetcher,
            rank=lambda resources: [r for r in resources if r.pk % 2 == 0],
        )

        pages_by_type = {}
        for call in fetcher.calls:
            pages_by_type.setdefault(call.step_type, []).append(call.page)
        # Practice has three steps (six links needed), so the hidden ones from page 0
        # force exactly one extra page. The other types already have enough.
        self.assertEqual(sorted(pages_by_type["practice"]), [0, 1])
        self.assertEqual(sorted(pages_by_type["read"]), [0])
        self.assertEqual(sorted(pages_by_type["watch"]), [0])

        practice_links = StepResource.objects.filter(
            step__plan=self.plan, step__step_type="practice"
        ).select_related("resource")
        self.assertEqual(practice_links.count(), 6)  # 3 steps x 2
        resource_ids = list(practice_links.values_list("resource_id", flat=True))
        self.assertEqual(len(resource_ids), len(set(resource_ids)))
        self.assertTrue(any("part 2" in link.resource.title for link in practice_links))

    def test_a_failed_refill_keeps_the_first_page(self):
        class PageOneFails:
            name = "page-one-fails"

            def find(self, query):
                if query.page:
                    raise FetchError("the second page is down")
                return FakeFetcher().find(query)

        with self.assertLogs("apps.resources.services", level="WARNING"):
            count = attach_resources(
                plan=self.plan,
                fetcher=PageOneFails(),
                rank=lambda resources: [r for r in resources if r.pk % 2 == 0],
            )
        self.assertEqual(count, 7)  # every step still got something from page 0
        shown = list(
            StepResource.objects.filter(step__plan=self.plan).values_list("resource_id", flat=True)
        )
        self.assertEqual(len(shown), len(set(shown)))

    def test_an_exhausted_source_stops_the_refill_loop(self):
        for step_type in ("practice", "read", "watch"):
            resources_for(topic=self.topic, step_type=step_type)
            ResourceFetch.objects.bulk_create(
                [
                    ResourceFetch(topic=self.topic, step_type=step_type, page=page)
                    for page in range(1, MAX_FETCH_PAGES)
                ]
            )
        fetcher = RecordingFetcher()
        count = attach_resources(plan=self.plan, fetcher=fetcher, rank=lambda resources: [])
        self.assertEqual(count, 0)
        self.assertEqual(fetcher.calls, [])  # the cap was reached without a request

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