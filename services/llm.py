"""The LLM boundary. Everything the rest of the app knows about "the AI" is here.
To use a real model later, add a client class with the same two members
(`name` and `generate_plan`) and register it in CLIENTS."""
from dataclasses import dataclass

from django.conf import settings


class LLMError(Exception):
    pass


@dataclass(frozen=True)
class PlanRequest:
    topic: str
    level: str
    goal: str
    hours_per_week: int
    weeks_available: int
    preferred_format: str
    depth: str


@dataclass(frozen=True)
class StepDraft:
    title: str
    explanation: str
    minutes: int
    step_type: str  # "read" | "watch" | "practice"


LEAD_TYPE = {"video": "watch", "reading": "read", "practice": "practice", "mix": "watch"}


class FakeLLMClient:
    """Deterministic and offline: the same request always gives the same plan.
    It exists so the whole flow can be built and tested without an API key."""

    name = "fake"

    def generate_plan(self, request: PlanRequest) -> list[StepDraft]:
        topic = request.topic
        lead = LEAD_TYPE[request.preferred_format]
        steps = []  # (title, explanation, step_type)

        if request.level == "beginner":
            steps.append((
                f"Foundations of {topic}",
                f"Start with the basic vocabulary and the big picture of {topic}, "
                "assuming no prior knowledge.",
                "read",
            ))
        steps.append((
            f"Core ideas of {topic}",
            f"Learn the central concepts of {topic} in the format that suits you.",
            lead,
        ))
        if request.depth == "deep":
            steps.append((
                f"Going deeper into {topic}",
                "Study the details and edge cases that separate beginners from confident users.",
                lead,
            ))
        steps.append((
            "Guided practice",
            f"Work through guided exercises to apply {topic} straight away.",
            "practice",
        ))
        if request.goal == "build" or request.depth == "deep":
            steps.append((
                f"Build a small {topic} project",
                "Combine what you learned in one small project you can show to others.",
                "practice",
            ))
        if request.goal == "exam":
            steps.append((
                "Practice questions and revision",
                "Test yourself under exam conditions and revisit the weak spots.",
                "practice",
            ))
        elif request.goal == "job":
            steps.append((
                "Real-world scenarios and interview prep",
                "Practice the situations and questions you are likely to face at work.",
                "practice",
            ))
        elif request.goal == "curiosity":
            steps.append((
                f"Explore ideas related to {topic}",
                "Follow whatever interests you most and connect it to what you learned.",
                "read",
            ))
        steps.append((
            "Review and next steps",
            "Summarize what you learned and choose what to study next.",
            "read",
        ))

        # Spread the user's time budget across the steps, within sane bounds.
        total_minutes = request.hours_per_week * request.weeks_available * 60
        per_step = max(15, min(240, total_minutes // len(steps)))
        per_step -= per_step % 5

        def final_type(natural_type):
            # A chosen format is respected strictly. Only "mix" varies by step.
        
            return natural_type if request.preferred_format == "mix" else lead

        return [StepDraft(t, e, per_step, final_type(kind)) for t, e, kind in steps]


CLIENTS = {"fake": FakeLLMClient}


def get_llm_client():
    backend = getattr(settings, "LLM_BACKEND", "fake")
    try:
        return CLIENTS[backend]()
    except KeyError:
        raise LLMError(f"Unknown LLM backend: {backend!r}") from None