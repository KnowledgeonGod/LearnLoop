"""Force the shared resource cache for a topic to be fetched again.

This marks the cached rows stale (without deleting them) and clears the page
cursors, so ``resources_for`` starts a fresh page-zero fetch. Resources that are
still linked to a plan are never deleted.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.resources.models import Resource, ResourceFetch
from apps.resources.services import CACHE_TTL, KINDS_FOR_STEP, ResourceFetchFailed, resources_for
from apps.topics.models import Topic


class Command(BaseCommand):
    help = "Refresh cached resources and pagination cursors for a topic (or --all)."

    def add_arguments(self, parser):
        parser.add_argument("--topic", type=int, help="Topic ID to refresh.")
        parser.add_argument(
            "--step-type",
            choices=("read", "watch", "practice"),
            help="Only refresh this step type (default: all three).",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Refresh every topic. This can make many provider calls.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be refreshed without changing anything.",
        )

    def handle(self, *args, **options):
        if not options["all"] and options["topic"] is None:
            raise CommandError("Pass --topic <id> or --all.")

        if options["all"]:
            topics = Topic.objects.all().order_by("pk")
        else:
            try:
                topics = [Topic.objects.get(pk=options["topic"])]
            except Topic.DoesNotExist:
                raise CommandError(f"Topic {options['topic']} does not exist.") from None

        step_types = (
            [options["step_type"]] if options["step_type"] else list(KINDS_FOR_STEP)
        )
        stale_before = timezone.now() - CACHE_TTL - timedelta(seconds=1)

        for topic in topics:
            for step_type in step_types:
                kinds = KINDS_FOR_STEP[step_type]
                count = Resource.objects.filter(topic=topic, kind__in=kinds).count()
                if options["dry_run"]:
                    self.stdout.write(
                        f"Would refresh {topic.name!r} / {step_type} ({count} cached resource(s))."
                    )
                    continue

                Resource.objects.filter(topic=topic, kind__in=kinds).update(
                    fetched_at=stale_before
                )
                ResourceFetch.objects.filter(topic=topic, step_type=step_type).delete()
                try:
                    resources_for(topic=topic, step_type=step_type)
                except ResourceFetchFailed as exc:
                    self.stdout.write(
                        self.style.WARNING(f"{topic.name!r} / {step_type}: {exc}")
                    )
                else:
                    self.stdout.write(
                        self.style.SUCCESS(f"Refreshed {topic.name!r} / {step_type}.")
                    )
