from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import LearningFormat


class Level(models.TextChoices):
    BEGINNER = "beginner", "I'm new to this"
    SOME = "some", "I know the basics"
    INTERMEDIATE = "intermediate", "I've used it before"


class Goal(models.TextChoices):
    EXAM = "exam", "Pass an exam"
    JOB = "job", "Get a job"
    BUILD = "build", "Build something"
    CURIOSITY = "curiosity", "Curiosity"


class Depth(models.TextChoices):
    OVERVIEW = "overview", "Quick overview"
    DEEP = "deep", "Deep mastery"


# The questions, in one place. Form, service and tests all use this tuple (DRY).
ANSWER_FIELDS = (
    "level", "goal", "hours_per_week", "weeks_available", "preferred_format", "depth",
)


class QuizAttempt(models.Model):
    """One user's answers for one topic. Re-taking the quiz updates the same row."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="quiz_attempts"
    )
    topic = models.ForeignKey(
        "topics.Topic", on_delete=models.CASCADE, related_name="quiz_attempts"
    )
    level = models.CharField(max_length=20, choices=Level.choices, default=Level.BEGINNER)
    goal = models.CharField(max_length=20, choices=Goal.choices, default=Goal.CURIOSITY)
    hours_per_week = models.PositiveSmallIntegerField(
        default=5, validators=[MinValueValidator(1), MaxValueValidator(40)]
    )
    weeks_available = models.PositiveSmallIntegerField(
        default=4, validators=[MinValueValidator(1), MaxValueValidator(52)]
    )
    preferred_format = models.CharField(
        max_length=20, choices=LearningFormat.choices, default=LearningFormat.MIX
    )
    depth = models.CharField(max_length=20, choices=Depth.choices, default=Depth.OVERVIEW)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "topic"], name="unique_quiz_user_topic"),
            models.CheckConstraint(condition=Q(level__in=Level.values), name="quiz_level_valid"),
            models.CheckConstraint(condition=Q(goal__in=Goal.values), name="quiz_goal_valid"),
            models.CheckConstraint(condition=Q(depth__in=Depth.values), name="quiz_depth_valid"),
            models.CheckConstraint(
                condition=Q(preferred_format__in=LearningFormat.values),
                name="quiz_format_valid",
            ),
            models.CheckConstraint(
                condition=Q(hours_per_week__range=(1, 40)), name="quiz_hours_range"
            ),
            models.CheckConstraint(
                condition=Q(weeks_available__range=(1, 52)), name="quiz_weeks_range"
            ),
        ]

    def __str__(self):
        return f"{self.user} / {self.topic}"