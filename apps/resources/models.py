from django.db import models
from django.db.models import Q
from django.utils import timezone

MAX_TITLE_LENGTH = 200
MAX_URL_LENGTH = 2000  # percent-encoded non-Latin topic names get long
MAX_SOURCE_LENGTH = 30
RESOURCES_PER_STEP = 2


class ResourceKind(models.TextChoices):
    VIDEO = "video", "Video"
    BOOK = "book", "Book"
    ARTICLE = "article", "Article"
    EXERCISE = "exercise", "Exercise"


class FetchStepType(models.TextChoices):
    READ = "read", "Read"
    WATCH = "watch", "Watch"
    PRACTICE = "practice", "Practice"


class Resource(models.Model):
    """Shared catalog entry: one row per (topic, url), reused by every user who studies
    that topic. It holds no user data. Feedback will attach to it later."""

    topic = models.ForeignKey("topics.Topic", on_delete=models.CASCADE, related_name="resources")
    kind = models.CharField(max_length=20, choices=ResourceKind.choices)
    title = models.CharField(max_length=MAX_TITLE_LENGTH)
    url = models.URLField(max_length=MAX_URL_LENGTH)
    source = models.CharField(max_length=MAX_SOURCE_LENGTH)  # which fetcher: "fake", "youtube"...
    fetched_at = models.DateTimeField(default=timezone.now)  # the cache timestamp

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["topic", "url"], name="unique_resource_topic_url"),
            models.CheckConstraint(condition=Q(kind__in=ResourceKind.values), name="resource_kind_valid"),
            models.CheckConstraint(condition=~Q(title=""), name="resource_title_not_blank"),
            # Defence in depth: a javascript: or data: link can never be stored,
            # even if app code forgets to validate.
            models.CheckConstraint(
                condition=Q(url__startswith="http://") | Q(url__startswith="https://"),
                name="resource_url_is_http",
            ),
        ]
        indexes = [
            # serves the cache lookup: topic + kind + not too old
            models.Index(fields=["topic", "kind", "-fetched_at"], name="resource_cache_lookup_idx"),
            models.Index(fields=["fetched_at"], name="resource_fetched_at_idx"),
        ]

    def __str__(self):
        return self.title


class ResourceFetch(models.Model):
    """Which page of a source was already fetched for a topic and step type.

    It is what makes refill move forward (page 1, 2, ...) instead of asking for
    page 0 again every rebuild. It holds no user data, so it can be shared."""

    topic = models.ForeignKey("topics.Topic", on_delete=models.CASCADE, related_name="resource_fetches")
    step_type = models.CharField(max_length=20, choices=FetchStepType.choices)
    page = models.PositiveIntegerField(default=0)
    fetched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["topic", "step_type", "page"], name="unique_resource_fetch_page"
            ),
            models.CheckConstraint(
                condition=Q(step_type__in=FetchStepType.values),
                name="resource_fetch_step_type_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["topic", "step_type", "-page"], name="resource_fetch_cursor_idx"),
            models.Index(fields=["fetched_at"], name="resource_fetch_fetched_idx"),
        ]

    def __str__(self):
        return f"{self.topic} / {self.step_type} / page {self.page}"


class StepResource(models.Model):
    """Links a plan step to its resources. Rebuilding a plan deletes the steps and
    these links with them, while the shared Resource rows survive."""

    step = models.ForeignKey("learning.PlanStep", on_delete=models.CASCADE, related_name="step_resources")
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name="step_links")
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["step", "resource"], name="unique_step_resource"),
            models.UniqueConstraint(fields=["step", "position"], name="unique_step_resource_position"),
            models.CheckConstraint(
                condition=Q(position__range=(1, RESOURCES_PER_STEP)),
                name="step_resource_position_range",
            ),
        ]

    def __str__(self):
        return f"{self.step_id}: {self.resource_id}"
    