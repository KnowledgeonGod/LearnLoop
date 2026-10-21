from django.conf import settings
from django.test.runner import DiscoverRunner


class OfflineBackendsTestRunner(DiscoverRunner):
    """Tests must not depend on the developer's .env or make real API calls.

    Individual tests can still opt in with override_settings.
    """

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        settings.LLM_BACKEND = "fake"
        settings.RESOURCE_BACKEND = "fake"
        settings.LLM_API_KEY = ""
        settings.YOUTUBE_API_KEY = ""
        settings.GITHUB_TOKEN = ""
