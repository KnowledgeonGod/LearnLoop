from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

# The limits live here once; the service and the constraints both use them (DRY).
MIN_STEPS, MAX_STEPS = 4, 8
MIN_MINUTES, MAX_MINUTES = 5, 480
MAX_TITLE_LENGTH = 200
MAX_EXPLANATION_LENGTH = 1000


class StepType(models.TextChoices):
    READ = "read", "Read"
    WATCH = "watch", "Watch"
    PRACTICE = "practice", "Practice"


class LearningPlan(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="learning_plans"
    )
    topic = models.ForeignKey(
        "topics.Topic", on_delete=models.CASCADE, related_name="learning_plans"
    )
    # Which version of the quiz answers this plan was built from. If the answers
    # change later, the plan is "stale" and the page offers to rebuild it.
    based_on_quiz_at = models.DateTimeField()
    generated_at = models.DateTimeField(default=timezone.now)
    source = models.CharField(max_length=30)  # which client made it, e.g. "fake"

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "topic"], name="unique_plan_user_topic"),
        ]

    def __str__(self):
        return f"{self.user} / {self.topic}"


class PlanJob(models.Model):
    """A background plan-generation request.

    The web request only creates a row and returns a progress page. A separate
    ``run_plan_worker`` process claims queued rows, so a slow provider call never
    holds an HTTP request or a database transaction open.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    ACTIVE_STATUSES = (Status.QUEUED, Status.RUNNING)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="plan_jobs"
    )
    topic = models.ForeignKey(
        "topics.Topic", on_delete=models.CASCADE, related_name="plan_jobs"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    # Safe to show: configuration messages are written by us, generic failures are
    # replaced with a neutral message by the worker.
    error = models.TextField(blank=True, default="")
    plan = models.ForeignKey(
        LearningPlan,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generation_jobs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # A user/topic can only have one queued-or-running job at a time. This
            # makes the enqueue path safe even if two browser tabs race.
            models.UniqueConstraint(
                fields=["user", "topic"],
                condition=Q(status__in=["queued", "running"]),
                name="unique_active_plan_job",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "created_at"], name="plan_job_queue_idx"),
            models.Index(fields=["user", "topic", "status"], name="plan_job_user_topic_idx"),
        ]

    def __str__(self):
        return f"{self.user} / {self.topic} / {self.status}"

    @property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES


class PlanUsage(models.Model):
    """How many plan builds one user has consumed today (cost control)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="plan_usage"
    )
    day = models.DateField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "day"], name="unique_plan_usage_user_day"),
        ]
        indexes = [models.Index(fields=["day"], name="plan_usage_day_idx")]

    def __str__(self):
        return f"{self.user} / {self.day}: {self.count}"


class LLMUsage(models.Model):
    """Provider token usage, grouped by day and model. No user data is stored."""

    day = models.DateField()
    model = models.CharField(max_length=100)
    calls = models.PositiveIntegerField(default=0)
    prompt_tokens = models.PositiveBigIntegerField(default=0)
    completion_tokens = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["day", "model"], name="unique_llm_usage_day_model"),
        ]
        indexes = [models.Index(fields=["day"], name="llm_usage_day_idx")]

    def __str__(self):
        return f"{self.day} / {self.model}: {self.calls} call(s)"


class PlanStep(models.Model):
    plan = models.ForeignKey(LearningPlan, on_delete=models.CASCADE, related_name="steps")
    position = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=MAX_TITLE_LENGTH)
    explanation = models.TextField()
    minutes = models.PositiveSmallIntegerField()
    step_type = models.CharField(max_length=20, choices=StepType.choices)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "position"], name="unique_step_position"),
            models.CheckConstraint(
                condition=Q(position__range=(1, MAX_STEPS)), name="step_position_range"
            ),
            models.CheckConstraint(
                condition=Q(minutes__range=(MIN_MINUTES, MAX_MINUTES)),
                name="step_minutes_range",
            ),
            models.CheckConstraint(
                condition=Q(step_type__in=StepType.values), name="step_type_valid"
            ),
            models.CheckConstraint(condition=~Q(title=""), name="step_title_not_blank"),
        ]

    def __str__(self):
        return f"{self.position}. {self.title}"