import io
import json
from itertools import product
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from django.test import SimpleTestCase, override_settings

from apps.learning.services import validate_drafts
from services.llm import (
    FakeLLMClient,
    LLMConfigurationError,
    LLMError,
    OpenAIClient,
    PlanRequest,
    _minutes,
    _post_json,
    _steps_from_text,
    get_llm_client,
)


def make_request(**overrides):
    values = dict(
        topic="Python", level="beginner", goal="job", hours_per_week=6,
        weeks_available=8, preferred_format="video", depth="deep",
    )
    return PlanRequest(**{**values, **overrides})


class FakeClientTests(SimpleTestCase):  # unit tests
    llm = FakeLLMClient()  # named "llm" because Django's test classes already own "client"

    def test_every_combination_produces_a_valid_plan(self):
        # The fake must never produce output our own validator would reject.
        levels = ("beginner", "some", "intermediate")
        goals = ("exam", "job", "build", "curiosity")
        depths = ("overview", "deep")
        formats = ("video", "reading", "practice", "mix")
        budgets = ((1, 1), (6, 8), (40, 52))
        for level, goal, depth, fmt, (hours, weeks) in product(levels, goals, depths, formats, budgets):
            with self.subTest(level=level, goal=goal, depth=depth, fmt=fmt, hours=hours, weeks=weeks):
                request = make_request(
                    level=level, goal=goal, depth=depth, preferred_format=fmt,
                    hours_per_week=hours, weeks_available=weeks,
                )
                validate_drafts(self.llm.generate_plan(request))

    def test_longest_topic_name_still_fits(self):  # edge case
        validate_drafts(self.llm.generate_plan(make_request(topic="x" * 100)))

    def test_is_deterministic(self):
        request = make_request()
        self.assertEqual(self.llm.generate_plan(request), self.llm.generate_plan(request))

    def test_more_demanding_answers_give_a_longer_plan(self):
        big = self.llm.generate_plan(make_request(level="beginner", depth="deep", goal="exam"))
        small = self.llm.generate_plan(make_request(level="intermediate", depth="overview", goal="curiosity"))
        self.assertGreater(len(big), len(small))

    def test_preferred_format_sets_the_lead_step_type(self):
        for fmt, expected in (("video", "watch"), ("reading", "read"), ("practice", "practice")):
            steps = self.llm.generate_plan(make_request(preferred_format=fmt))
            core = next(s for s in steps if s.title.startswith("Core ideas"))
            self.assertEqual(core.step_type, expected, fmt)

    def test_goal_changes_the_plan(self):
        exam = [s.title for s in self.llm.generate_plan(make_request(goal="exam"))]
        job = [s.title for s in self.llm.generate_plan(make_request(goal="job"))]
        self.assertIn("Practice questions and revision", exam)
        self.assertIn("Real-world scenarios and interview prep", job)

    def test_minutes_follow_the_time_budget(self):
        tiny = self.llm.generate_plan(make_request(hours_per_week=1, weeks_available=1))
        huge = self.llm.generate_plan(make_request(hours_per_week=40, weeks_available=52))
        self.assertEqual({s.minutes for s in tiny}, {15})
        self.assertEqual({s.minutes for s in huge}, {240})

    def test_topic_name_appears_in_titles(self):
        steps = self.llm.generate_plan(make_request(topic="Rust"))
        self.assertTrue(any("Rust" in s.title for s in steps))

    def test_a_chosen_format_applies_to_every_step(self):
        for fmt, expected in (("video", "watch"), ("reading", "read"), ("practice", "practice")):
            for goal in ("exam", "job", "build", "curiosity"):
                for depth in ("overview", "deep"):
                    with self.subTest(fmt=fmt, goal=goal, depth=depth):
                        steps = self.llm.generate_plan(
                            make_request(preferred_format=fmt, goal=goal, depth=depth)
                        )
                        self.assertEqual({s.step_type for s in steps}, {expected})

    def test_mix_uses_several_step_types(self):
        steps = self.llm.generate_plan(make_request(preferred_format="mix"))
        self.assertGreater(len({s.step_type for s in steps}), 1)


class GetClientTests(SimpleTestCase):
    def test_default_is_the_fake_client(self):
        self.assertIsInstance(get_llm_client(), FakeLLMClient)

    @override_settings(LLM_BACKEND="openai")
    def test_real_backend_is_selectable(self):
        self.assertIsInstance(get_llm_client(), OpenAIClient)

    @override_settings(LLM_BACKEND="nope")
    def test_unknown_backend_raises(self):
        with self.assertRaises(LLMError):
            get_llm_client()


class OpenAIClientTests(SimpleTestCase):  # unit tests, network always mocked
    llm = OpenAIClient()

    def content(self, text):
        return {"choices": [{"message": {"content": text}}]}

    @override_settings(LLM_API_KEY="test-key")
    @patch("services.llm._post_json")
    def test_a_valid_response_becomes_step_drafts(self, post):
        post.return_value = self.content(
            '{"steps": ['
            '{"title": "  Start   here ", "explanation": " Do this. ", "minutes": 30, "step_type": "read"},'
            '{"title": "Watch it", "explanation": "Then watch.", "minutes": 45.0, "step_type": "watch"},'
            '{"title": "Try it", "explanation": "Then build.", "minutes": "60", "step_type": "practice"},'
            '{"title": "Review", "explanation": "Finally review.", "minutes": 15, "step_type": "read"}'
            "]}"
        )
        steps = self.llm.generate_plan(make_request())
        self.assertEqual(len(steps), 4)
        self.assertEqual(steps[0].title, "Start here")
        self.assertEqual(steps[1].minutes, 45)
        self.assertEqual(steps[2].minutes, 60)
        validate_drafts(steps)  # the app's own validator accepts the output

    @override_settings(LLM_API_KEY="test-key")
    @patch("services.llm._post_json")
    def test_markdown_fences_and_prose_are_tolerated(self, post):
        post.return_value = self.content(
            "Here is your plan:\n```json\n"
            '{"steps": [{"title": "A", "explanation": "B", "minutes": 20, "step_type": "read"}]}\n'
            "```\nEnjoy!"
        )
        self.assertEqual(len(self.llm.generate_plan(make_request())), 1)

    @override_settings(LLM_API_KEY="test-key")
    @patch("services.llm._post_json")
    def test_a_bare_list_is_accepted(self, post):
        post.return_value = self.content(
            '[{"title": "A", "explanation": "B", "minutes": 20, "step_type": "read"}]'
        )
        self.assertEqual(len(self.llm.generate_plan(make_request())), 1)

    @override_settings(LLM_API_KEY="test-key")
    @patch("services.llm._post_json")
    def test_the_key_is_sent_as_a_header(self, post):
        post.return_value = self.content(
            '{"steps": [{"title": "A", "explanation": "B", "minutes": 20, "step_type": "read"}]}'
        )
        self.llm.generate_plan(make_request())
        _, kwargs = post.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")

    @override_settings(LLM_API_KEY="test-key")
    @patch("services.llm._post_json")
    def test_unusable_steps_are_skipped(self, post):
        post.return_value = self.content(
            '{"steps": ['
            '"not an object",'
            '{"title": 5, "explanation": "B", "minutes": 20, "step_type": "read"},'
            '{"title": "No minutes", "explanation": "B", "step_type": "read"},'
            '{"title": "Good", "explanation": "B", "minutes": 20, "step_type": "read"}'
            "]}"
        )
        steps = self.llm.generate_plan(make_request())
        self.assertEqual([s.title for s in steps], ["Good"])

    @override_settings(LLM_API_KEY="test-key")
    @patch("services.llm._post_json")
    def test_an_empty_or_invalid_answer_is_an_error(self, post):
        for content in ("", "hello", '{"steps": "nope"}', '{"steps": []}', "{}"):
            with self.subTest(content=content):
                post.return_value = self.content(content)
                with self.assertRaises(LLMError):
                    self.llm.generate_plan(make_request())

    @override_settings(LLM_API_KEY="test-key")
    @patch("services.llm._post_json")
    def test_a_response_without_content_is_an_error(self, post):
        for body in ({}, {"choices": []}, {"choices": [{}]}, {"choices": [{"message": {}}]}):
            with self.subTest(body=body):
                post.return_value = body
                with self.assertRaises(LLMError):
                    self.llm.generate_plan(make_request())

    @override_settings(LLM_API_KEY="test-key")
    @patch("services.llm._post_json")
    def test_a_truncated_response_is_reported_as_a_token_limit_problem(self, post):
        post.return_value = {
            "choices": [
                {
                    "message": {"content": '{"steps": [{"title": "A"'},
                    "finish_reason": "length",
                }
            ]
        }
        with self.assertRaisesMessage(LLMConfigurationError, "LLM_MAX_TOKENS"):
            self.llm.generate_plan(make_request())

    @override_settings(LLM_API_KEY="")
    def test_a_missing_key_is_a_configuration_error(self):
        with self.assertRaisesMessage(LLMConfigurationError, "LLM_API_KEY"):
            self.llm.generate_plan(make_request())
        with self.assertRaisesMessage(LLMConfigurationError, "check_backends"):
            self.llm.generate_plan(make_request())

    @override_settings(
        LLM_API_KEY="",
        LLM_API_URL="http://localhost:11434/v1/chat/completions",
    )
    @patch("services.llm._post_json")
    def test_a_local_endpoint_does_not_need_a_key(self, post):
        post.return_value = self.content(
            '{"steps": [{"title": "A", "explanation": "B", "minutes": 20, "step_type": "read"}]}'
        )
        self.llm.generate_plan(make_request())
        _, kwargs = post.call_args
        self.assertEqual(kwargs["headers"], {})

    def test_minutes_accepts_only_sane_values(self):
        self.assertEqual(_minutes(30), 30)
        self.assertEqual(_minutes(30.0), 30)
        self.assertEqual(_minutes("30"), 30)
        for bad in (True, False, 30.5, "thirty", None, [], {}):
            with self.subTest(bad=bad):
                self.assertIsNone(_minutes(bad))

    @override_settings(LLM_API_KEY="test-key")
    @patch("services.llm.urlopen", side_effect=OSError("offline"))
    def test_network_errors_become_llm_errors(self, _):
        with self.assertRaises(LLMError):
            self.llm.generate_plan(make_request())

    @override_settings(LLM_API_KEY="test-key", LLM_RETRIES=2, LLM_RETRY_DELAY=0)
    @patch("services.llm.time.sleep")
    @patch("services.llm.urlopen", side_effect=OSError("offline"))
    def test_network_errors_are_retried(self, urlopen_mock, sleep):
        with self.assertRaises(LLMError):
            self.llm.generate_plan(make_request())
        self.assertEqual(urlopen_mock.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_steps_from_text_rejects_non_json(self):
        with self.assertRaises(LLMError):
            _steps_from_text("no json here")

    def test_json_embedded_in_prose_is_found(self):
        steps = _steps_from_text(
            'Here you go: {"steps": [{"title": "A", "explanation": "B", '
            '"minutes": 20, "step_type": "read"}]} Enjoy!'
        )
        self.assertEqual([s.title for s in steps], ["A"])

    def test_incomplete_or_malformed_json_is_rejected(self):
        for text in ('{"steps": [', '{"steps": [}'):
            with self.subTest(text=text):
                with self.assertRaises(LLMError):
                    _steps_from_text(text)

    def test_a_non_string_step_type_is_skipped(self):
        with self.assertRaises(LLMError):
            _steps_from_text(
                '{"steps": [{"title": "A", "explanation": "B", "minutes": 20, "step_type": 5}]}'
            )


class PostJsonTests(SimpleTestCase):
    def response(self, raw):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = raw
        return response

    def test_a_valid_response_is_decoded(self):
        with patch("services.llm.urlopen", return_value=self.response(b'{"choices": []}')):
            self.assertEqual(_post_json("https://example.com", {}), {"choices": []})

    def test_invalid_json_is_an_llm_error(self):
        for raw in (b"not json", b"\xff"):
            with self.subTest(raw=raw):
                with patch("services.llm.urlopen", return_value=self.response(raw)):
                    with self.assertRaises(LLMError):
                        _post_json("https://example.com", {})

    def test_non_object_json_is_an_llm_error(self):
        with patch("services.llm.urlopen", return_value=self.response(b"[]")):
            with self.assertRaises(LLMError):
                _post_json("https://example.com", {})

    def test_provider_http_errors_are_actionable_and_do_not_include_the_key(self):
        cases = [
            (401, {}, LLMConfigurationError, "rejected the API key"),
            (404, {}, LLMConfigurationError, "LLM_MODEL"),
            (
                429,
                {"error": {"type": "insufficient_quota", "code": "credit_balance_exhausted"}},
                LLMConfigurationError,
                "no credits",
            ),
            (
                429,
                {"error": {"type": "rate_limit_exceeded"}},
                LLMError,
                "rate-limiting",
            ),
        ]
        for status, payload, expected, message in cases:
            with self.subTest(status=status, payload=payload):
                error = HTTPError(
                    "https://example.com",
                    status,
                    "error",
                    {},
                    io.BytesIO(json.dumps(payload).encode("utf-8")),
                )
                with patch("services.llm.urlopen", side_effect=error):
                    with self.assertRaises(expected) as caught:
                        _post_json("https://example.com", {})
                self.assertIs(type(caught.exception), expected)
                self.assertIn(message, str(caught.exception))
                self.assertNotIn("sk-", str(caught.exception))

    @override_settings(LLM_RETRIES=2, LLM_RETRY_DELAY=0)
    @patch("services.llm.time.sleep")
    @patch("services.llm.urlopen")
    def test_transient_provider_errors_are_retried(self, urlopen_mock, sleep):
        error = HTTPError("https://example.com", 503, "error", {}, io.BytesIO(b"{}"))
        urlopen_mock.side_effect = [error, self.response(b'{"choices": []}')]
        self.assertEqual(_post_json("https://example.com", {}), {"choices": []})
        self.assertEqual(urlopen_mock.call_count, 2)
        sleep.assert_called_once()

    @override_settings(LLM_RETRIES=2, LLM_RETRY_DELAY=0)
    @patch("services.llm.urlopen")
    def test_permanent_provider_errors_are_not_retried(self, urlopen_mock):
        error = HTTPError("https://example.com", 400, "error", {}, io.BytesIO(b"{}"))
        urlopen_mock.side_effect = error
        with self.assertRaises(LLMError):
            _post_json("https://example.com", {})
        self.assertEqual(urlopen_mock.call_count, 1)
