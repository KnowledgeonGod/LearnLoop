import logging

from django.conf import settings
from django.test.runner import DiscoverRunner


class OfflineBackendsTestRunner(DiscoverRunner):
    """Tests must not depend on the developer's .env or make real API calls.

    Individual tests can still opt in with override_settings.
    """

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        # Keep test output focused; assertLogs still captures what it needs.
        logging.getLogger().setLevel(logging.WARNING)
        for name in ("apps", "services", "django", "axes"):
            logging.getLogger(name).setLevel(logging.WARNING)
        settings.LLM_BACKEND = "fake"
        settings.RESOURCE_BACKEND = "fake"
        settings.LLM_API_KEY = ""
        settings.LLM_API_URL = "https://api.openai.com/v1/chat/completions"
        settings.LLM_MODEL = "gpt-4o-mini"
        settings.LLM_TIMEOUT = 30
        settings.LLM_MAX_TOKENS = 1200
        settings.YOUTUBE_API_KEY = ""
        settings.GITHUB_TOKEN = ""
        settings.LLM_RETRIES = 0
        settings.PLAN_JOBS_ENABLED = False
        settings.PLAN_DAILY_LIMIT = 0
        settings.LLM_USAGE_TRACKING = False
