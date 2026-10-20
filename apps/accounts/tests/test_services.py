from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.accounts.models import Profile, User
from apps.accounts.services import (
    EmailAlreadyRegistered,
    ensure_profile,
    record_format_signal,
    register_user,
    set_preferred_format,
)

PASSWORD = "S0me-str0ng-pass!"


class RegisterUserTests(TestCase):  # unit tests
    def test_creates_user_and_profile(self):
        user = register_user(name="Ada", email="ada@example.com", password=PASSWORD)
        self.assertTrue(Profile.objects.filter(user=user).exists())

    def test_email_is_normalized(self):  # edge case
        user = register_user(name="Ada", email="  Ada@Example.COM ", password=PASSWORD)
        self.assertEqual(user.email, "ada@example.com")

    def test_password_is_hashed(self):
        user = register_user(name="Ada", email="a@b.com", password=PASSWORD)
        self.assertNotEqual(user.password, PASSWORD)
        self.assertTrue(user.check_password(PASSWORD))

    def test_duplicate_email_is_case_insensitive(self):
        # Regression: A@b.com and a@b.com used to count as different accounts
        register_user(name="Ada", email="a@b.com", password=PASSWORD)
        with self.assertRaises(EmailAlreadyRegistered):
            register_user(name="Ada 2", email="A@B.com", password=PASSWORD)

    @patch("apps.accounts.services.ensure_profile", side_effect=RuntimeError)
    def test_failure_rolls_back_user(self, _mock):  # atomicity, via mocking
        with self.assertRaises(RuntimeError):
            register_user(name="Ada", email="a@b.com", password=PASSWORD)
        self.assertFalse(User.objects.filter(email="a@b.com").exists())

    def test_welcome_email_queued_only_after_commit(self):
        with patch("apps.accounts.services.send_welcome_email") as mocked:
            with self.captureOnCommitCallbacks(execute=True):
                user = register_user(name="Ada", email="a@b.com", password=PASSWORD)
        mocked.assert_called_once_with(user.pk)
        
    def test_create_user_without_email_raises(self):  # edge case
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", password=PASSWORD, name="X")

class ProfileServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):  # fixture: shared test data created once per class
        cls.user = register_user(name="Ada", email="ada@example.com", password=PASSWORD)

    def test_ensure_profile_is_idempotent(self):
        ensure_profile(self.user)
        ensure_profile(self.user)
        self.assertEqual(Profile.objects.filter(user=self.user).count(), 1)

    def test_set_format_twice_same_as_once(self):
        set_preferred_format(user=self.user, value="video")
        set_preferred_format(user=self.user, value="video")
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.preferred_format, "video")

    def test_invalid_format_rejected(self):  # edge case
        with self.assertRaises(ValueError):
            set_preferred_format(user=self.user, value="nonsense")
        with self.assertRaises(ValueError):
            record_format_signal(user=self.user, format_name="nonsense")

    def test_signals_accumulate(self):
        record_format_signal(user=self.user, format_name="video")
        profile = record_format_signal(user=self.user, format_name="video", weight=2)
        self.assertEqual(profile.format_scores["video"], 3)

    def test_database_rejects_invalid_format(self):
        # the DB constraint holds even when app code skips validation
        profile = ensure_profile(self.user)
        profile.preferred_format = "nonsense"
        with self.assertRaises(IntegrityError), transaction.atomic():
            profile.save()


class QueryCountTests(TestCase):
    def test_listing_users_with_profiles_is_one_query(self):  # N+1 guard
        for i in range(3):
            register_user(name=f"U{i}", email=f"u{i}@b.com", password=PASSWORD)
        with self.assertNumQueries(1):
            rows = [(u.email, u.profile.preferred_format) for u in User.objects.select_related("profile")]
        self.assertEqual(len(rows), 3)