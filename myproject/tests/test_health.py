from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse


class HealthCheckTests(TestCase):
    def test_health_check_reports_ok_without_secrets(self):
        with override_settings(
            LLM_BACKEND="fake", RESOURCE_BACKEND="fake", PLAN_JOBS_ENABLED=False
        ):
            resp = self.client.get(reverse("healthz"))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["database"], "ok")
        self.assertEqual(payload["llm_backend"], "fake")
        self.assertEqual(payload["resource_backend"], "fake")
        self.assertFalse(payload["plan_jobs_enabled"])
        self.assertEqual(payload["queued_plan_jobs"], 0)
        self.assertEqual(resp["Cache-Control"], "no-store")
        self.assertNotIn("key", {key.lower() for key in payload})

    def test_database_failure_returns_503(self):
        with patch("myproject.views._database_ok", side_effect=RuntimeError("down")):
            with self.assertLogs("myproject.views", level="ERROR"):
                resp = self.client.get(reverse("healthz"))
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["status"], "error")
        self.assertEqual(resp.json()["database"], "error")

    def test_health_check_is_get_only(self):
        self.assertEqual(self.client.post(reverse("healthz")).status_code, 405)
