from itertools import product

from django.test import SimpleTestCase, override_settings

from apps.learning.services import validate_drafts
from services.llm import FakeLLMClient, LLMError, PlanRequest, get_llm_client


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

    @override_settings(LLM_BACKEND="nope")
    def test_unknown_backend_raises(self):
        with self.assertRaises(LLMError):
            get_llm_client()