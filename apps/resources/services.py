import logging
from datetime import timedelta
from urllib.parse import urlsplit

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from services.fetchers import FetchError, ResourceQuery, get_fetcher
from services.ranking import diversify

from .models import (
    MAX_SOURCE_LENGTH,
    MAX_TITLE_LENGTH,
    MAX_URL_LENGTH,
    RESOURCES_PER_STEP,
    Resource,
    ResourceFetch,
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
# A source can serve this many pages per topic/step type. The cap keeps a rebuild
# from hammering a paid API forever when there is genuinely nothing left.
MAX_FETCH_PAGES = 5
MAX_REFILL_ROUNDS = MAX_FETCH_PAGES - 1


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


def _fresh_resources(topic, kinds) -> list:
    return list(
        Resource.objects.filter(
            topic=topic, kind__in=kinds, fetched_at__gte=timezone.now() - CACHE_TTL
        )
    )


def _fetch_page(*, topic, step_type, page, fetcher=None) -> list:
    """Fetch one page, save its usable resources, and remember that we asked.

    The network call runs outside any transaction, so no database lock is held while
    waiting. The caller decides how many pages it needs."""
    kinds = KINDS_FOR_STEP[step_type]
    try:
        fetcher = fetcher or get_fetcher()
        drafts = fetcher.find(ResourceQuery(topic=topic.name, step_type=step_type, page=page))
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

    # Record even a page that contained only duplicates. This is the cursor that lets
    # the next refill move to page + 1 instead of asking for the same page again.
    ResourceFetch.objects.update_or_create(
        topic=topic,
        step_type=step_type,
        page=page,
        defaults={"fetched_at": fetched_at},
    )
    return sorted(saved, key=_sort_key)


def resources_for(*, topic, step_type, fetcher=None) -> list:
    """Resources for a topic and step type. Shared across users and cached for CACHE_TTL,
    so the second person to study a topic costs no API calls."""
    try:
        kinds = KINDS_FOR_STEP[step_type]
    except KeyError:
        raise ValueError(f"Unknown step type: {step_type!r}") from None

    cached = sorted(_fresh_resources(topic, kinds), key=_sort_key)
    if cached:
        return cached

    # Empty or stale cache: start a fresh run from page 0. Dropping the cursor is what
    # lets an old topic pick up new resources after the cache expires.
    ResourceFetch.objects.filter(topic=topic, step_type=step_type).delete()
    return _fetch_page(topic=topic, step_type=step_type, page=0, fetcher=fetcher)


def refill_resources(*, topic, step_type, fetcher=None) -> list:
    """Fetch the next page of results for a topic and step type.

    Returns only the resources saved for that page, or [] when the source has run out.
    This is how a plan gets new material after the first page was exhausted by
    feedback, instead of repeating links the user already saw."""
    try:
        kinds = KINDS_FOR_STEP[step_type]
    except KeyError:
        raise ValueError(f"Unknown step type: {step_type!r}") from None

    if not _fresh_resources(topic, kinds):
        # A stale cache starts over at page 0 (and refreshes the stored rows).
        return resources_for(topic=topic, step_type=step_type, fetcher=fetcher)

    last_page = ResourceFetch.objects.filter(
        topic=topic, step_type=step_type
    ).aggregate(Max("page"))["page__max"]
    next_page = 0 if last_page is None else last_page + 1
    if next_page >= MAX_FETCH_PAGES:
        return []
    return _fetch_page(topic=topic, step_type=step_type, page=next_page, fetcher=fetcher)


def split_resources(resources, step_count, per_step=RESOURCES_PER_STEP) -> list:
    """Split one step type's resources between its steps so that NO resource is used
    twice anywhere in the plan. When the supply is short the resources are spread as
    evenly as possible: some steps get fewer links, and if there are fewer resources
    than steps the last ones get none. A repeat is never used to fill a gap."""
    step_count = max(step_count, 0)
    if not resources or not step_count:
        return [[] for _ in range(step_count)]

    supply = min(len(resources), step_count * per_step)
    base, extra = divmod(supply, step_count)
    sizes = [base + (1 if index < extra else 0) for index in range(step_count)]

    slices, start = [], 0
    for size in sizes:
        slices.append(resources[start:start + size])
        start += size
    return slices


def _ordered(resources, rank):
    """Apply a caller-supplied ranker defensively. A broken ranker must never cost the
    user their resources, so any problem falls back to the default order. A ranker may
    drop resources, but may not invent them or repeat them."""
    if rank is None:
        return resources
    try:
        ordered = list(rank(resources))
    except Exception:
        logger.exception("Ranking failed; keeping the default order")
        return resources
    pks = [resource.pk for resource in ordered]
    # An empty list is legitimate: the user may have hidden every resource.
    valid = len(pks) == len(set(pks)) and set(pks) <= {r.pk for r in resources}
    if not valid:
        logger.error("Ranker returned an invalid list; keeping the default order")
        return resources
    return ordered


def attach_resources(*, plan, fetcher=None, rank=None) -> int:
    """Best effort: a step type that can't be fetched just gets no links.
    `rank` is an optional function that orders (and may trim) one step type's resources
    for this person. Safe to call again. Returns how many steps got at least one resource.

    Each resource is linked at most once per plan, so rebuilding after feedback can
    never shuffle an already-shown link onto another step."""
    steps = list(plan.steps.all())  # ordered by position
    step_types = sorted({step.step_type for step in steps})

    found = {}
    for step_type in step_types:
        type_steps = [step for step in steps if step.step_type == step_type]
        try:
            fetched = resources_for(topic=plan.topic, step_type=step_type, fetcher=fetcher)
        except ResourceFetchFailed:
            logger.warning("No resources for %s steps of plan %s", step_type, plan.pk, exc_info=True)
            found[step_type] = []
            continue

        pool = list(fetched)
        ranked = diversify(_ordered(pool, rank))
        needed = len(type_steps) * RESOURCES_PER_STEP

        # The user may have hidden most of page 0. Ask the source for the next page
        # instead of reusing a hidden link; stop when enough is available or the
        # source runs out.
        for _ in range(MAX_REFILL_ROUNDS):
            if len(ranked) >= needed:
                break
            try:
                more = refill_resources(
                    topic=plan.topic, step_type=step_type, fetcher=fetcher
                )
            except ResourceFetchFailed:
                logger.warning(
                    "Refill failed for %s steps of plan %s", step_type, plan.pk, exc_info=True
                )
                break
            if not more:
                break
            seen = {resource.pk for resource in pool}
            pool.extend(resource for resource in more if resource.pk not in seen)
            ranked = diversify(_ordered(pool, rank))

        found[step_type] = ranked

    links, used = [], set()
    for step_type in step_types:
        type_steps = [step for step in steps if step.step_type == step_type]
        # The global `used` set is defence in depth: kinds normally cannot overlap.
        available = [r for r in found[step_type] if r.pk not in used]
        for step, picks in zip(type_steps, split_resources(available, len(type_steps))):
            used.update(resource.pk for resource in picks)
            for position, resource in enumerate(picks, start=1):
                links.append(StepResource(step=step, resource=resource, position=position))

    with transaction.atomic():  # old links and new links: all or nothing
        StepResource.objects.filter(step__in=steps).delete()
        StepResource.objects.bulk_create(links)
    return len({link.step.pk for link in links})