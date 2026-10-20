from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Topic(models.Model):
    """Shared across all users: 'Python', 'python ' and 'PYTHON' are one row."""

    name = models.CharField(max_length=100)  # as first typed, for display
    normalized_name = models.CharField(max_length=100, unique=True)  # lookup key
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~Q(normalized_name=""), name="topic_normalized_not_blank"
            ),
        ]

    def __str__(self):
        return self.name


class UserTopic(models.Model):
    """A user's search history: one row per (user, topic)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="topic_history"
    )
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="searches")
    last_searched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "topic"], name="unique_user_topic"),
        ]
        indexes = [
            # serves "this user's most recent searches", the dashboard query
            models.Index(fields=["user", "-last_searched_at"], name="usertopic_user_recent_idx"),
        ]