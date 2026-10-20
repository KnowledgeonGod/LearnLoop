from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.topics.services import record_search

PASSWORD = "S0me-str0ng-pass!"
BASE = timezone.now()


def make_user(email="ada@example.com"):
    return get_user_model().objects.create_user(email=email, password=PASSWORD, name="Test")


def search_at(user, name, hours=0):
    """Record a search at a fixed, controlled time so ordering tests never flake."""
    with patch("apps.topics.services.timezone.now", return_value=BASE + timedelta(hours=hours)):
        return record_search(user=user, raw_name=name)