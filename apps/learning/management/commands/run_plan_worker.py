"""Process queued plan-generation jobs.

Run this in a second terminal (or under a process manager) when
``PLAN_JOBS_ENABLED=1``:

    python manage.py run_plan_worker

``--once`` drains the current queue and exits, which is useful in tests and for a
cron-style deployment.
"""
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.learning.jobs import recover_stale_jobs, run_next_plan_job


class Command(BaseCommand):
    help = "Process queued learning-plan generation jobs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process everything currently queued, then exit.",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=getattr(settings, "PLAN_JOB_POLL_SECONDS", 2.0),
            help="Seconds to wait when the queue is empty (default: %(default)s).",
        )
        parser.add_argument(
            "--no-recover",
            action="store_true",
            help="Do not requeue jobs left RUNNING by a dead worker.",
        )

    def handle(self, *args, **options):
        if not options["no_recover"]:
            recovered = recover_stale_jobs()
            if recovered:
                self.stdout.write(self.style.WARNING(f"Requeued {recovered} stale job(s)."))

        if options["once"]:
            processed = 0
            while run_next_plan_job():
                processed += 1
            self.stdout.write(self.style.SUCCESS(f"Processed {processed} job(s)."))
            return

        sleep_for = max(float(options["sleep"]), 0.1)
        self.stdout.write(self.style.SUCCESS("Plan worker started. Press Ctrl+C to stop."))
        try:
            while True:
                if not run_next_plan_job():
                    time.sleep(sleep_for)
        except KeyboardInterrupt:
            self.stdout.write("\nPlan worker stopped.")
