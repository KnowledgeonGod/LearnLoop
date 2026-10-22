from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.learning.jobs import (
    GENERIC_ERROR,
    enqueue_plan_job,
    recover_stale_jobs,
    run_next_plan_job,
    run_plan_job,
)
from apps.learning.models import LearningPlan, PlanJob
from apps.learning.services import (
    PlanConfigurationFailed,
    PlanGenerationFailed,
    QuizRequired,
)
from apps.resources.models import StepResource

from .helpers import make_attempt, make_topic, make_user


class PlanJobServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.topic = make_topic()

    def test_enqueue_requires_a_quiz(self):
        with self.assertRaises(QuizRequired):
            enqueue_plan_job(user=self.user, topic=self.topic)
        self.assertEqual(PlanJob.objects.count(), 0)

    def test_enqueue_creates_a_queued_job(self):
        make_attempt(self.user, self.topic)
        job = enqueue_plan_job(user=self.user, topic=self.topic)
        self.assertEqual(job.status, PlanJob.Status.QUEUED)
        self.assertEqual(job.user, self.user)
        self.assertEqual(job.topic, self.topic)

    def test_enqueue_reuses_an_active_job(self):
        make_attempt(self.user, self.topic)
        first = enqueue_plan_job(user=self.user, topic=self.topic)
        second = enqueue_plan_job(user=self.user, topic=self.topic)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PlanJob.objects.count(), 1)

    def test_enqueue_creates_a_new_job_after_the_old_one_finished(self):
        make_attempt(self.user, self.topic)
        first = enqueue_plan_job(user=self.user, topic=self.topic)
        first.status = PlanJob.Status.SUCCEEDED
        first.save(update_fields=["status"])
        second = enqueue_plan_job(user=self.user, topic=self.topic)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(PlanJob.objects.count(), 2)

    def test_run_next_processes_the_job_and_attaches_resources(self):
        make_attempt(self.user, self.topic)
        job = enqueue_plan_job(user=self.user, topic=self.topic)
        self.assertTrue(run_next_plan_job())
        job.refresh_from_db()
        self.assertEqual(job.status, PlanJob.Status.SUCCEEDED)
        self.assertEqual(job.error, "")
        self.assertIsNotNone(job.plan)
        self.assertGreater(job.plan.steps.count(), 0)
        self.assertGreater(StepResource.objects.filter(step__plan=job.plan).count(), 0)
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)

    def test_running_a_job_twice_is_ignored(self):
        make_attempt(self.user, self.topic)
        job = enqueue_plan_job(user=self.user, topic=self.topic)
        self.assertTrue(run_plan_job(job.pk))
        self.assertFalse(run_plan_job(job.pk))
        self.assertEqual(LearningPlan.objects.filter(user=self.user, topic=self.topic).count(), 1)

    def test_a_generation_failure_is_generic_and_does_not_leak_details(self):
        make_attempt(self.user, self.topic)
        job = enqueue_plan_job(user=self.user, topic=self.topic)
        with patch("apps.learning.jobs.generate_plan", side_effect=PlanGenerationFailed("secret detail")):
            self.assertTrue(run_plan_job(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.status, PlanJob.Status.FAILED)
        self.assertEqual(job.error, GENERIC_ERROR)
        self.assertNotIn("secret detail", job.error)
        self.assertIsNone(job.plan)

    def test_a_configuration_failure_keeps_the_actionable_message(self):
        make_attempt(self.user, self.topic)
        job = enqueue_plan_job(user=self.user, topic=self.topic)
        message = "LLM_API_KEY is empty. Add it to .env."
        with patch(
            "apps.learning.jobs.generate_plan",
            side_effect=PlanConfigurationFailed(message),
        ):
            run_plan_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, PlanJob.Status.FAILED)
        self.assertEqual(job.error, message)

    def test_a_missing_quiz_is_reported(self):
        job = PlanJob.objects.create(user=self.user, topic=self.topic)
        with patch("apps.learning.jobs.generate_plan", side_effect=QuizRequired()):
            run_plan_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, PlanJob.Status.FAILED)
        self.assertIn("quiz", job.error.lower())

    def test_an_unexpected_crash_is_generic(self):
        make_attempt(self.user, self.topic)
        job = enqueue_plan_job(user=self.user, topic=self.topic)
        with patch("apps.learning.jobs.generate_plan", side_effect=RuntimeError("boom")):
            with self.assertLogs("apps.learning.jobs", level="ERROR"):
                run_plan_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, PlanJob.Status.FAILED)
        self.assertEqual(job.error, GENERIC_ERROR)

    def test_resource_attach_failure_does_not_fail_the_plan(self):
        make_attempt(self.user, self.topic)
        job = enqueue_plan_job(user=self.user, topic=self.topic)
        with patch("apps.learning.jobs.attach_resources", side_effect=RuntimeError("attach")):
            with self.assertLogs("apps.learning.jobs", level="ERROR"):
                run_plan_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, PlanJob.Status.SUCCEEDED)
        self.assertIsNotNone(job.plan)

    def test_stale_running_jobs_are_requeued(self):
        job = PlanJob.objects.create(user=self.user, topic=self.topic)
        PlanJob.objects.filter(pk=job.pk).update(
            status=PlanJob.Status.RUNNING,
            started_at=timezone.now() - timedelta(hours=2),
        )
        self.assertEqual(recover_stale_jobs(), 1)
        job.refresh_from_db()
        self.assertEqual(job.status, PlanJob.Status.QUEUED)
        self.assertIsNone(job.started_at)

    def test_fresh_running_jobs_are_not_requeued(self):
        job = PlanJob.objects.create(user=self.user, topic=self.topic)
        PlanJob.objects.filter(pk=job.pk).update(
            status=PlanJob.Status.RUNNING,
            started_at=timezone.now(),
        )
        self.assertEqual(recover_stale_jobs(), 0)
        job.refresh_from_db()
        self.assertEqual(job.status, PlanJob.Status.RUNNING)

    def test_worker_command_once_drains_the_queue(self):
        make_attempt(self.user, self.topic)
        job = enqueue_plan_job(user=self.user, topic=self.topic)
        out = StringIO()
        call_command("run_plan_worker", "--once", stdout=out)
        job.refresh_from_db()
        self.assertEqual(job.status, PlanJob.Status.SUCCEEDED)
        self.assertIn("Processed 1 job", out.getvalue())


@override_settings(PLAN_JOBS_ENABLED=True, RATELIMIT_ENABLE=False)
class PlanJobViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user("alice@example.com")
        cls.bob = make_user("bob@example.com")
        cls.topic = make_topic()
        cls.plan_url = reverse("plan", args=[cls.topic.pk])
        cls.generate_url = reverse("plan_generate", args=[cls.topic.pk])

    def setUp(self):
        self.client.force_login(self.alice)
        make_attempt(self.alice, self.topic)

    def test_generate_enqueues_and_redirects_to_progress(self):
        resp = self.client.post(self.generate_url)
        job = PlanJob.objects.get(user=self.alice, topic=self.topic)
        self.assertRedirects(
            resp,
            reverse("plan_job_progress", args=[self.topic.pk, job.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(job.status, PlanJob.Status.QUEUED)
        self.assertEqual(LearningPlan.objects.filter(user=self.alice).count(), 0)

    def test_a_second_click_reuses_the_active_job(self):
        self.client.post(self.generate_url)
        self.client.post(self.generate_url)
        self.assertEqual(PlanJob.objects.filter(user=self.alice, topic=self.topic).count(), 1)

    def test_generate_without_a_quiz_redirects_to_the_quiz(self):
        self.client.force_login(self.bob)
        resp = self.client.post(self.generate_url)
        self.assertRedirects(resp, reverse("quiz", args=[self.topic.pk]))
        self.assertEqual(PlanJob.objects.filter(user=self.bob).count(), 0)

    def test_progress_requires_login(self):
        job = enqueue_plan_job(user=self.alice, topic=self.topic)
        self.client.logout()
        resp = self.client.get(reverse("plan_job_progress", args=[self.topic.pk, job.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("login"), resp.url)

    def test_another_user_cannot_see_the_job(self):  # IDOR
        job = enqueue_plan_job(user=self.alice, topic=self.topic)
        self.client.force_login(self.bob)
        resp = self.client.get(reverse("plan_job_progress", args=[self.topic.pk, job.pk]))
        self.assertEqual(resp.status_code, 404)
        status = self.client.get(reverse("plan_job_status", args=[self.topic.pk, job.pk]))
        self.assertEqual(status.status_code, 404)

    def test_progress_page_shows_the_queued_state(self):
        job = enqueue_plan_job(user=self.alice, topic=self.topic)
        resp = self.client.get(reverse("plan_job_progress", args=[self.topic.pk, job.pk]))
        self.assertContains(resp, "building your plan")
        self.assertContains(resp, "queued")

    def test_succeeded_progress_redirects_to_the_plan(self):
        job = enqueue_plan_job(user=self.alice, topic=self.topic)
        run_plan_job(job.pk)
        resp = self.client.get(reverse("plan_job_progress", args=[self.topic.pk, job.pk]))
        self.assertRedirects(resp, self.plan_url)

    def test_failed_progress_shows_the_safe_error(self):
        job = enqueue_plan_job(user=self.alice, topic=self.topic)
        PlanJob.objects.filter(pk=job.pk).update(
            status=PlanJob.Status.FAILED, error="Something safe to show."
        )
        resp = self.client.get(reverse("plan_job_progress", args=[self.topic.pk, job.pk]))
        self.assertContains(resp, "Something safe to show")

    def test_status_json_is_no_store_and_contains_progress(self):
        job = enqueue_plan_job(user=self.alice, topic=self.topic)
        resp = self.client.get(reverse("plan_job_status", args=[self.topic.pk, job.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Cache-Control"], "no-store")
        self.assertEqual(resp.json()["status"], PlanJob.Status.QUEUED)
        self.assertIsNone(resp.json()["redirect_url"])

    def test_status_json_redirects_when_succeeded(self):
        job = enqueue_plan_job(user=self.alice, topic=self.topic)
        run_plan_job(job.pk)
        resp = self.client.get(reverse("plan_job_status", args=[self.topic.pk, job.pk]))
        self.assertEqual(resp.json()["status"], PlanJob.Status.SUCCEEDED)
        self.assertEqual(resp.json()["redirect_url"], self.plan_url)

    def test_plan_page_links_to_an_active_job(self):
        job = enqueue_plan_job(user=self.alice, topic=self.topic)
        resp = self.client.get(self.plan_url)
        self.assertContains(resp, "already in progress")
        self.assertContains(resp, reverse("plan_job_progress", args=[self.topic.pk, job.pk]))
