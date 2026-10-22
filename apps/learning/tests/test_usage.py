from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.learning.jobs import enqueue_plan_job, run_plan_job
from apps.learning.models import LLMUsage, PlanJob, PlanUsage
from apps.learning.services import (
    PlanConfigurationFailed,
    PlanGenerationFailed,
    PlanQuotaExceeded,
    ensure_plan_quota_available,
    generate_plan,
    plan_usage_remaining,
)
from apps.learning.usage import record_llm_usage
from apps.resources.models import ResourceFetch
from services.llm import StepDraft

from .helpers import ConfigFailingClient, FailingClient, make_attempt, make_topic, make_user


class UsageClient:
    name = "usage"
    last_model = "test-model"
    last_usage = {"prompt_tokens": 10, "completion_tokens": 20}

    def generate_plan(self, request):
        return [
            StepDraft("Step 1", "Do this.", 30, "read"),
            StepDraft("Step 2", "Do that.", 30, "watch"),
            StepDraft("Step 3", "Practice.", 30, "practice"),
            StepDraft("Step 4", "Review.", 30, "read"),
        ]


@override_settings(PLAN_DAILY_LIMIT=2, LLM_USAGE_TRACKING=False)
class PlanQuotaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.topic = make_topic()

    def setUp(self):
        make_attempt(self.user, self.topic)

    def test_the_limit_is_enforced(self):
        generate_plan(user=self.user, topic=self.topic)
        generate_plan(user=self.user, topic=self.topic)
        with self.assertRaisesMessage(PlanQuotaExceeded, "limit of 2"):
            generate_plan(user=self.user, topic=self.topic)
        self.assertEqual(
            PlanUsage.objects.get(user=self.user, day=timezone.localdate()).count, 2
        )
        self.assertEqual(plan_usage_remaining(self.user), 0)

    def test_another_user_has_their_own_allowance(self):
        generate_plan(user=self.user, topic=self.topic)
        other = make_user("other@example.com")
        make_attempt(other, self.topic)
        generate_plan(user=other, topic=self.topic)
        self.assertEqual(plan_usage_remaining(other), 1)
        self.assertEqual(plan_usage_remaining(self.user), 1)

    def test_an_exhausted_allowance_can_be_checked_without_consuming(self):
        PlanUsage.objects.create(user=self.user, day=timezone.localdate(), count=2)
        with self.assertRaises(PlanQuotaExceeded):
            ensure_plan_quota_available(self.user)
        self.assertEqual(
            PlanUsage.objects.get(user=self.user, day=timezone.localdate()).count, 2
        )

    def test_enqueue_refuses_an_exhausted_allowance(self):
        PlanUsage.objects.create(user=self.user, day=timezone.localdate(), count=2)
        with self.assertRaises(PlanQuotaExceeded):
            enqueue_plan_job(user=self.user, topic=self.topic)
        self.assertEqual(PlanJob.objects.count(), 0)

    @override_settings(PLAN_DAILY_LIMIT=1)
    def test_an_active_job_is_reused_even_when_the_allowance_is_used(self):
        job = enqueue_plan_job(user=self.user, topic=self.topic)
        PlanUsage.objects.create(user=self.user, day=timezone.localdate(), count=1)
        self.assertEqual(enqueue_plan_job(user=self.user, topic=self.topic).pk, job.pk)

    @override_settings(PLAN_DAILY_LIMIT=1)
    def test_a_failed_generation_does_not_consume_the_allowance(self):
        with self.assertRaises(PlanConfigurationFailed):
            generate_plan(user=self.user, topic=self.topic, client=ConfigFailingClient())
        with self.assertRaises(PlanGenerationFailed):
            generate_plan(user=self.user, topic=self.topic, client=FailingClient())
        self.assertFalse(PlanUsage.objects.exists())

    def test_a_worker_job_reports_the_quota_failure_safely(self):
        PlanUsage.objects.create(user=self.user, day=timezone.localdate(), count=2)
        job = PlanJob.objects.create(user=self.user, topic=self.topic)
        run_plan_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, PlanJob.Status.FAILED)
        self.assertIn("limit of 2", job.error)

    @override_settings(PLAN_DAILY_LIMIT=0)
    def test_zero_means_unlimited(self):
        for _ in range(5):
            generate_plan(user=self.user, topic=self.topic)
        self.assertIsNone(plan_usage_remaining(self.user))
        self.assertFalse(PlanUsage.objects.exists())

    @override_settings(PLAN_DAILY_LIMIT=1, RATELIMIT_ENABLE=False)
    def test_the_plan_page_shows_the_remaining_count(self):
        generate_plan(user=self.user, topic=self.topic)
        self.client.force_login(self.user)
        resp = self.client.get(reverse("plan", args=[self.topic.pk]))
        self.assertContains(resp, "0 plan builds left today")


@override_settings(LLM_USAGE_TRACKING=True)
class LLMUsageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.topic = make_topic()

    def setUp(self):
        make_attempt(self.user, self.topic)

    def test_a_real_client_usage_is_recorded_and_aggregated(self):
        client = UsageClient()
        generate_plan(user=self.user, topic=self.topic, client=client)
        row = LLMUsage.objects.get(day=timezone.localdate(), model="test-model")
        self.assertEqual(row.calls, 1)
        self.assertEqual(row.prompt_tokens, 10)
        self.assertEqual(row.completion_tokens, 20)

        generate_plan(user=self.user, topic=self.topic, client=client)
        row.refresh_from_db()
        self.assertEqual(row.calls, 2)
        self.assertEqual(row.prompt_tokens, 20)
        self.assertEqual(row.completion_tokens, 40)

    def test_a_client_without_usage_records_nothing(self):
        class PlainClient:
            name = "plain"

            def generate_plan(self, request):
                return UsageClient().generate_plan(request)

        generate_plan(user=self.user, topic=self.topic, client=PlainClient())
        self.assertFalse(LLMUsage.objects.exists())

    def test_invalid_usage_is_ignored(self):
        class BadUsageClient(UsageClient):
            last_usage = {"prompt_tokens": "not-a-number"}

        record_llm_usage(BadUsageClient())
        self.assertFalse(LLMUsage.objects.exists())

    @override_settings(LLM_USAGE_TRACKING=False)
    def test_tracking_can_be_disabled(self):
        record_llm_usage(UsageClient())
        self.assertFalse(LLMUsage.objects.exists())

    def test_a_usage_recording_failure_does_not_break_a_plan(self):
        with patch("apps.learning.usage.LLMUsage.objects.get_or_create", side_effect=RuntimeError):
            with self.assertLogs("apps.learning.usage", level="ERROR"):
                generate_plan(user=self.user, topic=self.topic, client=UsageClient())
        self.assertTrue(self.user.learning_plans.exists())


@override_settings(PLAN_DAILY_LIMIT=1, RATELIMIT_ENABLE=False, PLAN_JOBS_ENABLED=False)
class QuotaViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.topic = make_topic()
        make_attempt(self.user, self.topic)
        self.client.force_login(self.user)
        self.url = reverse("plan_generate", args=[self.topic.pk])

    def test_second_generation_is_rate_limited_by_the_daily_quota(self):
        self.assertRedirects(self.client.post(self.url), reverse("plan", args=[self.topic.pk]))
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 429)
        self.assertContains(resp, "limit of 1", status_code=429)

    @override_settings(PLAN_JOBS_ENABLED=True)
    def test_enqueue_shows_the_quota_message(self):
        PlanUsage.objects.create(user=self.user, day=timezone.localdate(), count=1)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 429)
        self.assertContains(resp, "limit of 1", status_code=429)
        self.assertFalse(PlanJob.objects.exists())


class UsageReportTests(TestCase):
    def test_report_prints_llm_resource_and_job_totals(self):
        user = make_user()
        topic = make_topic()
        LLMUsage.objects.create(
            day=timezone.localdate(),
            model="test-model",
            calls=2,
            prompt_tokens=10,
            completion_tokens=20,
        )
        ResourceFetch.objects.create(topic=topic, step_type="watch", page=0)
        PlanJob.objects.create(user=user, topic=topic, status=PlanJob.Status.SUCCEEDED)

        out = StringIO()
        call_command("usage_report", "--days", "7", stdout=out)
        text = out.getvalue()
        self.assertIn("test-model", text)
        self.assertIn("calls=2", text)
        self.assertIn("pages=1", text)
        self.assertIn("succeeded: 1", text)
