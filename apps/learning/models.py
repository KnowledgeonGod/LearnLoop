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