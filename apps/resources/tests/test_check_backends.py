from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings


class CheckBackendsTests(SimpleTestCase):
    @override_settings(LLM_BACKEND="fake", RESOURCE_BACKEND="fake")
    def test_fake_backends_need_no_keys(self):
        out = StringIO()
        call_command("check_backends", stdout=out)
        text = out.getvalue()
        self.assertIn("backend: fake", text)
        self.assertIn("no key needed", text)
        self.assertIn("Backend configuration looks usable", text)

    @override_settings(
        LLM_BACKEND="openai",
        LLM_API_KEY="",
        LLM_API_URL="https://api.openai.com/v1/chat/completions",
        RESOURCE_BACKEND="fake",
    )
    def test_a_missing_openai_key_exits_with_an_error(self):
        out = StringIO()
        with self.assertRaises(SystemExit) as caught:
            call_command("check_backends", stdout=out)
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("MISSING", out.getvalue())
        self.assertIn("LLM_API_KEY", out.getvalue())

    @override_settings(
        LLM_BACKEND="fake",
        RESOURCE_BACKEND="real",
        YOUTUBE_API_KEY="",
        GITHUB_TOKEN="",
    )
    def test_a_missing_youtube_key_is_a_warning_not_a_failure(self):
        out = StringIO()
        call_command("check_backends", stdout=out)
        text = out.getvalue()
        self.assertIn("YouTube key: MISSING", text)
        self.assertIn("Backend configuration looks usable", text)

    @override_settings(
        LLM_BACKEND="fake",
        RESOURCE_BACKEND="fake",
    )
    def test_test_mode_uses_the_fakes_without_network_calls(self):
        out = StringIO()
        call_command("check_backends", "--test", stdout=out)
        text = out.getvalue()
        self.assertIn("LLM: fake client, no network call", text)
        self.assertIn("Resources: fake fetcher, no network call", text)
