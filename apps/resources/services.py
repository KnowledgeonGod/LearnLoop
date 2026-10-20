import logging
from datetime import timedelta
from urllib.parse import urlsplit

from django.db import transaction
from django.utils import timezone

from services.fetchers import FetchError, ResourceQuery, get_fetcher

from .models import (
    MAX_SOURCE_LENGTH,
    MAX_TITLE_LENGTH,
    MAX_URL_LENGTH,
    RESOURCES_PER_STEP,
    Resource,
    ResourceKind,
    StepResource,
)

logger = logging.getLogger(__name__)

# Plain strings on purpose, so comparisons never depend on enum behaviour.
KINDS_FOR_STEP = {
    "watch": [ResourceKind.VIDEO.value],
    "read": [ResourceKind.BOOK.value, ResourceKind.ARTICLE.value],
    "practice": [ResourceKind.EXERCISE.value],
}
CACHE_TTL = timedelta(days=7)
MAX_PER_FETCH = 10


class ResourceFetchFailed(Exception):
    pass


def _clean_one(draft, allowed_kinds):
    """Return (kind, title, url, source), or None if this draft is unusable."""
    values = [getattr(draft, name, None) for name in ("kind", "title", "url", "source")]
    if not all(isinstance(v, str) for v in values):
        return None
    kind, title, url, source = values
    title = " ".join(title.split())
    url = url.strip()
    source = source.strip()

    if kind not in allowed_kinds:
        return None
    if not title or len(title) > MAX_TITLE_LENGTH:
        return None
    if not source or len(source) > MAX_SOURCE_LENGTH:
        return None
    if len(url) > MAX_URL_LENGTH or any(ch.isspace() for ch in url):
        return None
    # Only plain http(s) links. This blocks javascript:, data:, file: and the like.
    if not url.startswith(("http://", "https://")):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:  # e.g. "http://[::1"
        return None
    if not host or parts.username or parts.password:
        return None
    return kind, title, url, source


def clean_drafts(drafts, allowed_kinds) -> list:
    """Third-party data is untrusted. Keep the usable items, drop the junk and duplicates."""
    try:
        drafts = list(drafts)
    except TypeError:
        return []
    cleaned, seen_urls = [], set()
    for draft in drafts:
        item = _clean_one(draft, allowed_kinds)
        if item is None or item[2] in seen_urls:
            continue
        seen_urls.add(item[2])
        cleaned.append(item)
        if len(cleaned) == MAX_PER_FETCH:
            break
    return cleaned


def _sort_key(resource):
    return (resource.kind, resource.pk)


def resources_for(*, topic, step_type, fetcher=None) -> list:
    """Resources for a topic and step type. Shared across users and cached for CACHE_TTL,
    so the second person to study a topic costs no API calls."""
    try:
        kinds = KINDS_FOR_STEP[step_type]
    except KeyError:
        raise ValueError(f"Unknown step type: {step_type!r}") from None

    cached = list(
        Resource.objects.filter(
            topic=topic, kind__in=kinds, fetched_at__gte=timezone.now() - CACHE_TTL
        )
    )
    if cached:
        return sorted(cached, key=_sort_key)

    # The slow network call happens outside any transaction: no lock is held while waiting.
    try:
        fetcher = fetcher or get_fetcher()
        drafts = fetcher.find(ResourceQuery(topic=topic.name, step_type=step_type))
    except FetchError as exc:
        raise ResourceFetchFailed(str(exc)) from exc

    cleaned = clean_drafts(drafts, kinds)
    if not cleaned:
        raise ResourceFetchFailed("The source returned no usable resources.")

    fetched_at = timezone.now()
    saved = []
    for kind, title, url, source in cleaned:
        # Idempotent and race-safe: backed by the unique (topic, url) constraint, so two
        # users triggering the same fetch at once produce one row each, not an error.
        resource, _ = Resource.objects.update_or_create(
            topic=topic,
            url=url,
            defaults={"kind": kind, "title": title, "source": source, "fetched_at": fetched_at},
        )
        saved.append(resource)
    return sorted(saved, key=_sort_key)


def pick_resources(resources, occurrence, per_step=RESOURCES_PER_STEP) -> list:
    """Give the Nth step of a type a different slice, so three practice steps
    don't all show the same two links."""
    if not resources:
        return []
    start = (occurrence * per_step) % len(resources)
    rotated = resources[start:] + resources[:start]
    return rotated[:per_step]


def attach_resources(*, plan, fetcher=None) -> int:
    """Best effort: a step type that can't be fetched just gets no links.
    Safe to call again. Returns how many steps got at least one resource."""
    steps = list(plan.steps.all())  # ordered by position

    found = {}
    for step_type in sorted({step.step_type for step in steps}):
        try:
            found[step_type] = resources_for(topic=plan.topic, step_type=step_type, fetcher=fetcher)
        except ResourceFetchFailed:
            logger.warning("No resources for %s steps of plan %s", step_type, plan.pk, exc_info=True)
            found[step_type] = []

    links, seen = [], {}
    for step in steps:
        occurrence = seen.get(step.step_type, 0)
        seen[step.step_type] = occurrence + 1
        picks = pick_resources(found[step.step_type], occurrence)
        for position, resource in enumerate(picks, start=1):
            links.append(StepResource(step=step, resource=resource, position=position))

    with transaction.atomic():  # old links and new links: all or nothing
        StepResource.objects.filter(step__in=steps).delete()
        StepResource.objects.bulk_create(links)
    return len({link.step.pk for link in links})