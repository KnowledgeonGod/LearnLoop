"""The LLM boundary. Everything the rest of the app knows about "the AI" is here.
To use a real model later, add a client class with the same two members
(`name` and `generate_plan`) and register it in CLIENTS."""
import json
import re
from dataclasses import asdict, dataclass
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

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


SYSTEM_PROMPT = """You design short, practical learning plans for one person.
Return ONLY a JSON object of this exact shape:
{"steps": [{"title": "...", "explanation": "...", "minutes": 30, "step_type": "read"}]}

Rules:
- Return 4 to 8 steps, in the order the learner should do them.
- step_type is exactly one of: "read", "watch", "practice".
- minutes is a whole number from 5 to 480, realistic for one sitting.
- title is at most 200 characters. explanation is at most 1000 characters, plain text.
- Respect the preferred format: "video" means every step is "watch"; "reading"
  means every step is "read"; "practice" means every step is "practice"; "mix"
  should use all three step types.
- Match the level, goal, depth and weekly time budget in the request.
- No markdown, no code fences, no commentary outside the JSON."""

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _post_json(url, payload, *, headers=None) -> dict:
    """POST JSON and return the decoded response. Raises LLMError on any failure."""
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Some hosted models reject requests without a user agent.
            "User-Agent": "LearnLoop/1.0",
            **(headers or {}),
        },
    )
    try:
        with urlopen(request, timeout=getattr(settings, "LLM_TIMEOUT", 30)) as response:
            raw = response.read()
    except (URLError, OSError, TimeoutError) as exc:
        raise LLMError(f"The language model request failed: {exc}") from exc

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LLMError("The language model returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise LLMError("The language model returned an unexpected response.")
    return data


def _extract_json(text):
    """Models sometimes wrap JSON in prose or a Markdown fence. Find the payload."""
    if not isinstance(text, str) or not text.strip():
        raise LLMError("The language model returned an empty answer.")
    fenced = _JSON_BLOCK.search(text)
    candidate = fenced.group(1) if fenced else text
    candidate = candidate.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        starts = [index for index in (candidate.find("{"), candidate.find("[")) if index != -1]
        if not starts:
            raise LLMError("The language model did not return JSON.") from None
        end = max(candidate.rfind("}"), candidate.rfind("]"))
        if end <= min(starts):
            raise LLMError("The language model did not return complete JSON.") from None
        try:
            return json.loads(candidate[min(starts):end + 1])
        except json.JSONDecodeError:
            raise LLMError("The language model did not return valid JSON.") from None


def _minutes(value):
    """Accept 30, 30.0 or "30" from a model, but reject booleans and nonsense."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _steps_from_text(text) -> list[StepDraft]:
    payload = _extract_json(text)
    raw_steps = payload.get("steps") if isinstance(payload, dict) else payload
    if not isinstance(raw_steps, list):
        raise LLMError("The language model did not return a list of steps.")

    steps = []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            continue
        title = raw.get("title")
        explanation = raw.get("explanation")
        step_type = raw.get("step_type")
        if not isinstance(title, str) or not isinstance(explanation, str):
            continue
        if not isinstance(step_type, str):
            continue
        minutes = _minutes(raw.get("minutes"))
        if minutes is None:
            continue
        steps.append(
            StepDraft(
                title=" ".join(title.split()),
                explanation=explanation.strip(),
                minutes=minutes,
                step_type=step_type.strip(),
            )
        )
    if not steps:
        raise LLMError("The language model returned no usable steps.")
    return steps


class OpenAIClient:
    """A real client for OpenAI's chat-completions API and compatible endpoints.

    Configure it with LLM_BACKEND=openai plus LLM_API_KEY, LLM_MODEL and optionally
    LLM_API_URL (for example a local Ollama or vLLM server) and LLM_TIMEOUT.
    The key travels in an Authorization header, never in the URL."""

    name = "openai"

    def generate_plan(self, request: PlanRequest) -> list[StepDraft]:
        api_url = getattr(settings, "LLM_API_URL", "https://api.openai.com/v1/chat/completions")
        api_key = getattr(settings, "LLM_API_KEY", "")
        # OpenAI itself requires a key. A local server (Ollama, vLLM) usually does not,
        # so only enforce the key for the hosted default.
        if not api_key and urlsplit(api_url).hostname == "api.openai.com":
            raise LLMError("LLM_API_KEY is not set, so the openai backend cannot run.")

        payload = {
            "model": getattr(settings, "LLM_MODEL", "gpt-4o-mini"),
            "temperature": 0.4,
            "max_tokens": getattr(settings, "LLM_MAX_TOKENS", 1200),
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(asdict(request), ensure_ascii=False)},
            ],
        }
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        data = _post_json(api_url, payload, headers=headers)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("The language model response had no message content.") from None
        return _steps_from_text(content)


CLIENTS = {"fake": FakeLLMClient, "openai": OpenAIClient}


def get_llm_client():
    backend = getattr(settings, "LLM_BACKEND", "fake")
    try:
        return CLIENTS[backend]()
    except KeyError:
        raise LLMError(f"Unknown LLM backend: {backend!r}") from None