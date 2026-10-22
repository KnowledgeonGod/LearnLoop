"""Show recent LLM token usage, resource fetches, and plan-job volume."""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from apps.learning.models import LLMUsage, PlanJob
from apps.resources.models import ResourceFetch


class Command(BaseCommand):
    help = "Report recent provider/resource usage and plan-job volume."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="How many days to include (default: %(default)s).",
        )

    def handle(self, *args, **options):
        days = max(int(options["days"]), 1)
        since = timezone.localdate() - timedelta(days=days - 1)

        self.stdout.write(self.style.MIGRATE_HEADING(f"LLM usage (since {since})"))
        llm_rows = LLMUsage.objects.filter(day__gte=since).order_by("day", "model")
        if not llm_rows.exists():
            self.stdout.write("  no LLM calls recorded")
        for row in llm_rows:
            self.stdout.write(
                f"  {row.day}  {row.model}  calls={row.calls}  "
                f"prompt_tokens={row.prompt_tokens}  completion_tokens={row.completion_tokens}"
            )

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(f"Resource fetches (since {since})"))
        fetch_rows = (
            ResourceFetch.objects.filter(fetched_at__date__gte=since)
            .values("fetched_at__date", "topic__name", "step_type")
            .annotate(pages=Count("id"))
            .order_by("fetched_at__date", "topic__name", "step_type")
        )
        if not fetch_rows:
            self.stdout.write("  no resource fetches recorded")
        for row in fetch_rows:
            self.stdout.write(
                f"  {row['fetched_at__date']}  {row['topic__name']}  "
                f"{row['step_type']}  pages={row['pages']}"
            )

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(f"Plan jobs (since {since})"))
        job_rows = (
            PlanJob.objects.filter(created_at__date__gte=since)
            .values("status")
            .annotate(count=Count("id"))
            .order_by("status")
        )
        if not job_rows:
            self.stdout.write("  no plan jobs recorded")
        for row in job_rows:
            self.stdout.write(f"  {row['status']}: {row['count']}")
