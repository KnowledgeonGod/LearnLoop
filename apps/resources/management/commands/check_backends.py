"""Show which planner and resource source are selected, and what is missing.

Run this after editing .env. It never prints secret values.
"""
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.learning.services import InvalidPlan, validate_drafts
from services.fetchers import FakeFetcher, FetchError, ResourceQuery, get_fetcher
from services.llm import FakeLLMClient, LLMError, PlanRequest, get_llm_client


class Command(BaseCommand):
    help = "Check the LLM and resource backend configuration. Use --test for a live call."

    def add_arguments(self, parser):
        parser.add_argument(
            "--test",
            action="store_true",
            help="Make one small live request to each configured backend (may cost tokens).",
        )

    def handle(self, *args, **options):
        ok = True
        self.stdout.write(self.style.MIGRATE_HEADING("LLM backend"))
        ok = self._check_llm() and ok

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Resource backend"))
        ok = self._check_resources() and ok

        if options["test"]:
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING("Live checks"))
            ok = self._test_llm() and ok
            ok = self._test_resources() and ok

        if not ok:
            self.stdout.write("")
            self.stdout.write(
                self.style.ERROR(
                    "Configuration problems found. See .env.example or README.md, "
                    "then run this command again."
                )
            )
            raise SystemExit(1)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Backend configuration looks usable."))

    def _check_llm(self):
        backend = getattr(settings, "LLM_BACKEND", "fake")
        self.stdout.write(f"  backend: {backend}")

        if backend == "fake":
            self.stdout.write(self.style.SUCCESS("  offline planner; no key needed"))
            return True
        if backend != "openai":
            self.stdout.write(self.style.ERROR(f"  unknown backend: {backend!r}"))
            return False

        api_url = getattr(settings, "LLM_API_URL", "https://api.openai.com/v1/chat/completions")
        api_key = getattr(settings, "LLM_API_KEY", "")
        host = urlsplit(api_url).hostname
        self.stdout.write(f"  url: {api_url}")
        self.stdout.write(f"  model: {getattr(settings, 'LLM_MODEL', 'gpt-4o-mini')}")

        if api_key and api_key.strip():
            self.stdout.write(self.style.SUCCESS("  api key: set"))
            return True
        if host == "api.openai.com":
            self.stdout.write(
                self.style.ERROR(
                    "  api key: MISSING - set LLM_API_KEY in .env, or use LLM_BACKEND=fake"
                )
            )
            return False

        self.stdout.write(
            self.style.WARNING("  api key: not set (allowed for a local OpenAI-compatible server)")
        )
        return True

    def _check_resources(self):
        backend = getattr(settings, "RESOURCE_BACKEND", "fake")
        self.stdout.write(f"  backend: {backend}")

        if backend == "fake":
            self.stdout.write(self.style.SUCCESS("  offline resources; no keys needed"))
            return True
        if backend != "real":
            self.stdout.write(self.style.ERROR(f"  unknown backend: {backend!r}"))
            return False

        youtube_key = getattr(settings, "YOUTUBE_API_KEY", "")
        github_token = getattr(settings, "GITHUB_TOKEN", "")
        if youtube_key and youtube_key.strip():
            self.stdout.write(self.style.SUCCESS("  YouTube key: set (watch steps)"))
        else:
            self.stdout.write(
                self.style.WARNING(
                    "  YouTube key: MISSING - watch steps will have no resources"
                )
            )
        self.stdout.write(self.style.SUCCESS("  Open Library / Wikipedia / Google Books: no key needed (read steps)"))
        if github_token and github_token.strip():
            self.stdout.write(self.style.SUCCESS("  GitHub token: set (practice steps)"))
        else:
            self.stdout.write("  GitHub token: not set (optional; only raises the rate limit)")
        return True

    def _test_llm(self):
        try:
            client = get_llm_client()
        except LLMError as exc:
            self.stdout.write(self.style.ERROR(f"  LLM: {exc}"))
            return False

        if isinstance(client, FakeLLMClient):
            self.stdout.write(self.style.SUCCESS("  LLM: fake client, no network call"))
            return True

        request = PlanRequest(
            topic="Python",
            level="beginner",
            goal="job",
            hours_per_week=4,
            weeks_available=4,
            preferred_format="mix",
            depth="overview",
        )
        try:
            drafts = client.generate_plan(request)
        except LLMError as exc:
            self.stdout.write(self.style.ERROR(f"  LLM: failed: {exc}"))
            return False

        try:
            validate_drafts(drafts)
        except InvalidPlan as exc:
            self.stdout.write(self.style.ERROR(f"  LLM: returned an invalid plan: {exc}"))
            return False

        self.stdout.write(self.style.SUCCESS(f"  LLM: returned {len(drafts)} valid steps"))
        return True

    def _test_resources(self):
        try:
            fetcher = get_fetcher()
        except FetchError as exc:
            self.stdout.write(self.style.ERROR(f"  Resources: {exc}"))
            return False

        if isinstance(fetcher, FakeFetcher):
            self.stdout.write(self.style.SUCCESS("  Resources: fake fetcher, no network call"))
            return True

        youtube_key = getattr(settings, "YOUTUBE_API_KEY", "")
        ok = True
        for step_type in ("read", "practice", "watch"):
            try:
                drafts = fetcher.find(ResourceQuery("Python", step_type))
            except FetchError as exc:
                if step_type == "watch" and not (youtube_key and youtube_key.strip()):
                    self.stdout.write(self.style.WARNING(f"  {step_type}: skipped: {exc}"))
                    continue
                self.stdout.write(self.style.ERROR(f"  {step_type}: failed: {exc}"))
                ok = False
                continue

            if drafts:
                self.stdout.write(self.style.SUCCESS(f"  {step_type}: {len(drafts)} resources"))
            else:
                self.stdout.write(self.style.WARNING(f"  {step_type}: returned no resources"))
        return ok
