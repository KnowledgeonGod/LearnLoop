from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone


class LearningFormat(models.TextChoices):
    VIDEO = "video", "Video"
    READING = "reading", "Reading"
    PRACTICE = "practice", "Hands-on practice"
    MIX = "mix", "Mix of everything"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra_fields)
        user.set_password(password)  # hashing: never store the raw password
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    name = models.CharField(max_length=150)  # NOT NULL by default
    email = models.EmailField(unique=True)   # unique + automatically indexed
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    class Meta:
        # Database constraints: enforced by the DB even if app code is wrong
        constraints = [
            models.UniqueConstraint(Lower("email"), name="unique_user_email_ci"),
            models.CheckConstraint(condition=~Q(name=""), name="user_name_not_blank"),
        ]

    def __str__(self):
        return self.email


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")  # FK constraint
    preferred_format = models.CharField(
        max_length=20, choices=LearningFormat.choices, default=LearningFormat.MIX
    )
    # Learned from behavior, e.g. {"video": 3.0, "reading": 1.0}
    format_scores = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(preferred_format__in=LearningFormat.values),
                name="profile_format_valid",
            ),
        ]

    def __str__(self):
        return f"Profile of {self.user.email}"