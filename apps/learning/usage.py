"""Provider usage accounting. Best effort: never break a plan over a stats row."""
import logging

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import LLMUsage

logger = logging.getLogger(__name__)


def record_llm_usage(client) -> None:
    if not getattr(settings, "LLM_USAGE_TRACKING", True):
        return
    usage = getattr(client, "last_usage", None)
    if not isinstance(usage, dict):
        return

    model = getattr(client, "last_model", None) or getattr(client, "name", None) or "unknown"
    if not isinstance(model, str):
        model = "unknown"
    model = model[:100]

    try:
        prompt_tokens = max(int(usage.get("prompt_tokens") or 0), 0)
        completion_tokens = max(int(usage.get("completion_tokens") or 0), 0)
    except (TypeError, ValueError):
        return

    try:
        with transaction.atomic():
            row, created = LLMUsage.objects.get_or_create(
                day=timezone.localdate(),
                model=model,
                defaults={
                    "calls": 1,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                },
            )
            if not created:
                LLMUsage.objects.filter(pk=row.pk).update(
                    calls=F("calls") + 1,
                    prompt_tokens=F("prompt_tokens") + prompt_tokens,
                    completion_tokens=F("completion_tokens") + completion_tokens,
                )
    except Exception:
        logger.exception("Could not record LLM usage; the plan is unaffected")
