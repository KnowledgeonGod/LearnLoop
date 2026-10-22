"""Delete old, unlinked resource cache rows and stale pagination cursors."""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.resources.models import Resource, ResourceFetch


class Command(BaseCommand):
    help = "Delete cached resources and fetch cursors older than N days."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Age in days; newer rows are kept (default: %(default)s).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deleted without changing anything.",
        )

    def handle(self, *args, **options):
        days = max(int(options["days"]), 1)
        cutoff = timezone.now() - timedelta(days=days)

        # A resource that is still linked to a plan is never deleted; the plan is
        # the user's data and must keep working even when the cache is old.
        resources = Resource.objects.filter(fetched_at__lt=cutoff, step_links__isnull=True)
        fetches = ResourceFetch.objects.filter(fetched_at__lt=cutoff)
        resource_count, fetch_count = resources.count(), fetches.count()

        if options["dry_run"]:
            self.stdout.write(
                f"Would delete {resource_count} unlinked resource(s) and "
                f"{fetch_count} fetch cursor(s) older than {cutoff.isoformat()}."
            )
            return

        resources.delete()
        fetches.delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {resource_count} unlinked resource(s) and "
                f"{fetch_count} fetch cursor(s) older than {cutoff.isoformat()}."
            )
        )
