"""Small, public health endpoint for load balancers and uptime checks."""
import logging

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


def _database_ok():
    connection.ensure_connection()
    return True


@require_GET
def healthz(request):
    database_ok = True
    try:
        database_ok = _database_ok()
    except Exception:
        database_ok = False
        logger.exception("Health check: database connection failed")

    queued_jobs = None
    if database_ok:
        try:
            from apps.learning.models import PlanJob

            queued_jobs = PlanJob.objects.filter(status=PlanJob.Status.QUEUED).count()
        except Exception:
            # The database is reachable but the schema may not be migrated yet.
            logger.exception("Health check: could not read the plan job queue")

    payload = {
        "status": "ok" if database_ok else "error",
        "database": "ok" if database_ok else "error",
        "llm_backend": getattr(settings, "LLM_BACKEND", "unknown"),
        "resource_backend": getattr(settings, "RESOURCE_BACKEND", "unknown"),
        "plan_jobs_enabled": bool(getattr(settings, "PLAN_JOBS_ENABLED", False)),
        "queued_plan_jobs": queued_jobs,
    }
    response = JsonResponse(payload, status=200 if database_ok else 503)
    response["Cache-Control"] = "no-store"
    return response
