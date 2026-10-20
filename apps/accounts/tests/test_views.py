from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.services import register_user

PASSWORD = "S0me-str0ng-pass!"
VALID = {"name": "Ada", "email": "ada@example.com", "password1": PASSWORD, "password2": PASSWORD}


@override_settings(RATELIMIT_ENABLE=False)
class SignupViewTests(TestCase):  # integration tests

    def test_signup_logs_in_and_redirects(self):
        resp = self.client.post(reverse("signup"), VALID)
        self.assertRedirects(resp, reverse("dashboard"))
        self.assertEqual(User.objects.count(), 1)

    def test_weak_password_rejected(self):
        resp = self.client.post(reverse("signup"), {**VALID, "password1": "123", "password2": "123"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(User.objects.count(), 0)

    def test_password_similar_to_email_rejected(self):  # edge case
        bad = "ada@example.com"
        resp = self.client.post(reverse("signup"), {**VALID, "password1": bad, "password2": bad})
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(resp.status_code, 200)

    def test_duplicate_email_shows_form_error(self):
        register_user(name="Ada", email="ada@example.com", password=PASSWORD)
        resp = self.client.post(reverse("signup"), {**VALID, "email": "ADA@example.com"})
        self.assertContains(resp, "already exists")

    def test_password_mismatch_rejected(self):
        resp = self.client.post(reverse("signup"), {**VALID, "password2": "different-Pass-1!"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "do not match")
        self.assertEqual(User.objects.count(), 0)

    def test_logged_in_user_is_redirected_away_from_signup(self):
        user = register_user(name="Ada", email="ada@example.com", password=PASSWORD)
        self.client.force_login(user)
        self.assertRedirects(self.client.get(reverse("signup")), reverse("dashboard"))


class AuthorizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = register_user(name="Alice", email="alice@example.com", password=PASSWORD)
        cls.bob = register_user(name="Bob", email="bob@example.com", password=PASSWORD)

    def test_pages_require_login(self):
        for name in ("dashboard", "profile"):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 302)
            self.assertIn(reverse("login"), resp.url)

    def test_user_can_only_change_own_profile(self):  # IDOR
        self.client.force_login(self.alice)
        self.client.post(reverse("profile"), {"preferred_format": "video"})
        self.alice.profile.refresh_from_db()
        self.bob.profile.refresh_from_db()
        self.assertEqual(self.alice.profile.preferred_format, "video")
        self.assertEqual(self.bob.profile.preferred_format, "mix")

    def test_post_without_csrf_token_is_rejected(self):  # CSRF
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.alice)
        resp = client.post(reverse("profile"), {"preferred_format": "video"})
        self.assertEqual(resp.status_code, 403)

    def test_superuser_without_profile_can_open_profile_page(self):  # regression
        admin = User.objects.create_superuser(email="root@example.com", password=PASSWORD, name="Root")
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("profile")).status_code, 200)


class RateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_signup_blocked_after_too_many_attempts(self):
        for _ in range(10):
            self.assertEqual(self.client.post(reverse("signup"), {}).status_code, 200)
        self.assertEqual(self.client.post(reverse("signup"), {}).status_code, 403)

    def test_login_locked_after_repeated_failures(self):
        register_user(name="Ada", email="ada@example.com", password=PASSWORD)
        for _ in range(5):
            self.client.post(reverse("login"), {"username": "ada@example.com", "password": "wrong"})
        resp = self.client.post(reverse("login"), {"username": "ada@example.com", "password": PASSWORD})
        self.assertEqual(resp.status_code, 429)  # even the right password is blocked now

class AdminTests(TestCase):
    def test_admin_user_list_loads_with_and_without_profiles(self):
        admin = User.objects.create_superuser(email="root@example.com", password=PASSWORD, name="Root")
        register_user(name="Ada", email="ada@example.com", password=PASSWORD)
        self.client.force_login(admin)
        resp = self.client.get("/admin/accounts/user/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ada@example.com")