from django.conf import settings
from django.db import models
from django.db.models import Q


class Rating(models.TextChoices):
    LIKE = "like", "Like"
    DISLIKE = "dislike", "Dislike"


class Progress(models.TextChoices):
    DONE = "done", "Done"
    SKIPPED = "skipped", "Skipped"


class ResourceFeedback(models.Model):
    """One row per (user, resource). Rating and progress are independent: you can
    like something and also mark it done. A row that carries neither does not exist."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="resource_feedback"
    )
    resource = models.ForeignKey(
        "resources.Resource", on_delete=models.CASCADE, related_name="feedback"
    )
    rating = models.CharField(max_length=10, choices=Rating.choices, null=True, blank=True)
    progress = models.CharField(max_length=10, choices=Progress.choices, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # The unique constraint also creates the index that serves "this user's feedback".
        constraints = [
            models.UniqueConstraint(fields=["user", "resource"], name="unique_feedback_user_resource"),
            models.CheckConstraint(
                condition=Q(rating__isnull=True) | Q(rating__in=Rating.values),
                name="feedback_rating_valid",
            ),
            models.CheckConstraint(
                condition=Q(progress__isnull=True) | Q(progress__in=Progress.values),
                name="feedback_progress_valid",
            ),
            models.CheckConstraint(
                condition=Q(rating__isnull=False) | Q(progress__isnull=False),
                name="feedback_not_empty",
            ),
        ]

    def __str__(self):
        return f"{self.user_id} / {self.resource_id}"