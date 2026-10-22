"""Database-backed background plan generation.

No Redis or Celery is required. The web request creates a ``PlanJob`` row; a
separate ``run_plan_worker`` process claims queued rows and does the slow work.
"""
import logging
import time
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from apps.quiz.selectors import get_attempt
from apps.resources.services import attach_resources

from .models import PlanJob
from .personalize import ranker_for
from .services import (
    PlanConfigurationFailed,
    PlanGenerationFailed,
    PlanQuotaExceeded,
    QuizRequired,
    ensure_plan_quota_available,
    generate_plan,
)

logger = logging.getLogger(__name__)

GENERIC_ERROR = "We couldn't build your plan right now. Please try again in a moment."
QUIZ_REQUIRED_ERROR = "Take the quiz first, then create your plan."
ACTIVE_STATUSES = (PlanJob.Status.QUEUED, PlanJob.Status.RUNNING)


def enqueue_plan_job(*, user, topic) -> PlanJob:
    """Return the active job for this user/topic, or create a queued one."""
    if get_attempt(user, topic) is None:
        raise QuizRequired()

    active = (
        PlanJob.objects.filter(user=user, topic=topic, status__in=ACTIVE_STATUSES)
        .order_by("-created_at")
        .first()
    )
    if active is not None:
        return active

    ensure_plan_quota_available(user)

    try:
        return PlanJob.objects.create(user=user, topic=topic)
    except IntegrityError:
        # Another request won the partial unique constraint race.
        return (
            PlanJob.objects.filter(user=user, topic=topic, status__in=ACTIVE_STATUSES)
            .order_by("-created_at")
            .first()
        )


def _claim(job_id) -> bool:
    """Atomically move one QUEUED job to RUNNING. Exactly one worker wins."""
    return bool(
        PlanJob.objects.filter(pk=job_id, status=PlanJob.Status.QUEUED).update(
            status=PlanJob.Status.RUNNING,
            started_at=timezone.now(),
            error="",
            finished_at=None,
        )
    )


def _finish(job, status, *, error="", plan=None):
    job.status = status
    job.error = error
    job.plan = plan
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "error", "plan", "finished_at"])


def run_plan_job(job_id) -> bool:
    """Claim and run one job. Returns False when another worker already took it."""
    if not _claim(job_id):
        return False

    try:
        job = PlanJob.objects.select_related("user", "topic").get(pk=job_id)
    except PlanJob.DoesNotExist:
        return False

    started = time.monotonic()
    try:
        plan = generate_plan(user=job.user, topic=job.topic)
        try:
            # Resource fetching is best effort: a plan without links is still useful.
            attach_resources(plan=plan, rank=ranker_for(job.user, job.topic))
        except Exception:
            logger.exception("Background resource attach failed (job=%s, plan=%s)", job.pk, plan.pk)

        _finish(job, PlanJob.Status.SUCCEEDED, plan=plan)
        logger.info(
            "Plan job succeeded (job=%s, user=%s, topic=%s, plan=%s, duration=%.2fs)",
            job.pk, job.user_id, job.topic_id, plan.pk, time.monotonic() - started,
        )
    except QuizRequired:
        _finish(job, PlanJob.Status.FAILED, error=QUIZ_REQUIRED_ERROR)
        logger.warning("Plan job had no quiz (job=%s, user=%s, topic=%s)", job.pk, job.user_id, job.topic_id)
    except PlanQuotaExceeded as exc:
        _finish(job, PlanJob.Status.FAILED, error=str(exc))
        logger.warning("Plan job quota exceeded (job=%s, user=%s)", job.pk, job.user_id)
    except PlanConfigurationFailed as exc:
        # Written by us and safe to show; never contains a key.
        _finish(job, PlanJob.Status.FAILED, error=str(exc))
        logger.warning("Plan job backend is not configured (job=%s): %s", job.pk, exc)
    except PlanGenerationFailed:
        _finish(job, PlanJob.Status.FAILED, error=GENERIC_ERROR)
        logger.exception("Plan job failed (job=%s, user=%s, topic=%s)", job.pk, job.user_id, job.topic_id)
    except Exception:
        _finish(job, PlanJob.Status.FAILED, error=GENERIC_ERROR)
        logger.exception(
            "Plan job crashed (job=%s, user=%s, topic=%s)", job.pk, job.user_id, job.topic_id
        )
    return True


def run_next_plan_job() -> bool:
    """Run the oldest queued job, if any. Returns True when a job was attempted."""
    job_id = (
        PlanJob.objects.filter(status=PlanJob.Status.QUEUED)
        .order_by("created_at")
        .values_list("pk", flat=True)
        .first()
    )
    if job_id is None:
        return False
    run_plan_job(job_id)
    return True


def recover_stale_jobs() -> int:
    """Requeue jobs whose worker disappeared while they were RUNNING."""
    minutes = max(int(getattr(settings, "PLAN_JOB_STALE_MINUTES", 30)), 1)
    cutoff = timezone.now() - timedelta(minutes=minutes)
    stale = PlanJob.objects.filter(status=PlanJob.Status.RUNNING).filter(
        Q(started_at__isnull=True) | Q(started_at__lt=cutoff)
    )
    count = stale.update(
        status=PlanJob.Status.QUEUED,
        started_at=None,
        finished_at=None,
        error="",
    )
    if count:
        logger.warning("Requeued %s stale plan job(s) older than %s minutes", count, minutes)
    return count


def worker_may_be_stopped(job) -> bool:
    """True when a queued job has waited long enough to suggest a missing worker."""
    if job.status != PlanJob.Status.QUEUED:
        return False
    seconds = max(int(getattr(settings, "PLAN_JOB_WORKER_HINT_SECONDS", 10)), 1)
    return timezone.now() - job.created_at > timedelta(seconds=seconds)
