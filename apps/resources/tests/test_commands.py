from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.resources.models import Resource, ResourceFetch, StepResource

from .helpers import make_plan, make_topic, make_user


class PruneResourcesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.topic = make_topic()
        cls.old = timezone.now() - timedelta(days=60)

    def make_resource(self, title, url, *, fetched_at=None, kind="video"):
        return Resource.objects.create(
            topic=self.topic,
            kind=kind,
            title=title,
            url=url,
            source="test",
            fetched_at=fetched_at or timezone.now(),
        )

    def test_old_unlinked_resources_and_cursors_are_removed(self):
        old_unlinked = self.make_resource("Old", "https://example.com/old", fetched_at=self.old)
        fresh = self.make_resource("Fresh", "https://example.com/fresh")
        old_fetch = ResourceFetch.objects.create(
            topic=self.topic, step_type="watch", page=3, fetched_at=self.old
        )
        fresh_fetch = ResourceFetch.objects.create(
            topic=self.topic, step_type="read", page=0, fetched_at=timezone.now()
        )

        call_command("prune_resources", "--days", "30", stdout=StringIO())

        self.assertFalse(Resource.objects.filter(pk=old_unlinked.pk).exists())
        self.assertTrue(Resource.objects.filter(pk=fresh.pk).exists())
        self.assertFalse(ResourceFetch.objects.filter(pk=old_fetch.pk).exists())
        self.assertTrue(ResourceFetch.objects.filter(pk=fresh_fetch.pk).exists())

    def test_a_resource_linked_to_a_plan_is_kept_even_when_old(self):
        user = make_user()
        plan = make_plan(user, self.topic)
        linked = self.make_resource("Linked", "https://example.com/linked", fetched_at=self.old)
        StepResource.objects.create(step=plan.steps.first(), resource=linked, position=1)

        call_command("prune_resources", "--days", "30", stdout=StringIO())

        self.assertTrue(Resource.objects.filter(pk=linked.pk).exists())

    def test_dry_run_changes_nothing(self):
        old = self.make_resource("Old", "https://example.com/old", fetched_at=self.old)
        out = StringIO()
        call_command("prune_resources", "--days", "30", "--dry-run", stdout=out)
        self.assertTrue(Resource.objects.filter(pk=old.pk).exists())
        self.assertIn("Would delete 1", out.getvalue())


class RefreshResourcesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.topic = make_topic()

    def test_refresh_marks_the_cache_stale_and_fetches_again(self):
        old = Resource.objects.create(
            topic=self.topic,
            kind="video",
            title="Old video",
            url="https://example.com/old",
            source="test",
        )
        ResourceFetch.objects.create(topic=self.topic, step_type="watch", page=4)

        out = StringIO()
        call_command(
            "refresh_resources",
            "--topic",
            str(self.topic.pk),
            "--step-type",
            "watch",
            stdout=out,
        )

        old.refresh_from_db()
        self.assertLess(old.fetched_at, timezone.now() - timedelta(days=1))
        self.assertFalse(
            ResourceFetch.objects.filter(topic=self.topic, step_type="watch", page=4).exists()
        )
        self.assertIn("Refreshed", out.getvalue())

    def test_dry_run_makes_no_changes(self):
        old = Resource.objects.create(
            topic=self.topic,
            kind="video",
            title="Old video",
            url="https://example.com/old",
            source="test",
        )
        before = old.fetched_at
        out = StringIO()
        call_command(
            "refresh_resources",
            "--topic",
            str(self.topic.pk),
            "--step-type",
            "watch",
            "--dry-run",
            stdout=out,
        )
        old.refresh_from_db()
        self.assertEqual(old.fetched_at, before)
        self.assertIn("Would refresh", out.getvalue())

    def test_topic_is_required(self):
        with self.assertRaises(CommandError):
            call_command("refresh_resources", stdout=StringIO())

    def test_unknown_topic_is_an_error(self):
        with self.assertRaises(CommandError):
            call_command("refresh_resources", "--topic", "999999", stdout=StringIO())
