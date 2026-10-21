"""Which resource is best for THIS person. Pure functions: no database and no Django,
so every rule can be tested exactly. All the weights live in one table of constants.

Only the person's OWN feedback is ever considered. One learner's likes and dislikes
never change what somebody else is shown."""
import re
from dataclasses import dataclass

# Vocabulary shared with the feedback models (a test keeps these in sync).
LIKE, DISLIKE = "like", "dislike"
DONE, SKIPPED = "done", "skipped"

W_OWN_LIKE = 5.0
W_OWN_DISLIKE = -50.0
W_OWN_SKIPPED = -30.0
W_OWN_DONE = -10.0
SOURCE_TRUST = {
    "youtube": 3.0,
    "openlibrary": 3.0,
    "wikipedia": 3.0,
    "github": 3.0,
}  # real results beat generic search links

# (points for beginner-style titles, points for advanced-style titles), by quiz level
LEVEL_RULES = {
    "beginner": (2.0, -2.0),
    "some": (0.0, -1.0),
    "intermediate": (-1.0, 2.0),
}
BEGINNER_TITLE = re.compile(
    r"\b(beginners?|introduction|introductory|intro|basics?|crash course|"
    r"getting started|101|simple|simply|explained)\b",
    re.IGNORECASE,
)
ADVANCED_TITLE = re.compile(
    r"\b(advanced|expert|in-depth|in depth|deep dive|masterclass|mastery|internals)\b",
    re.IGNORECASE,
)

REASON_TEXT = {
    "own_like": "You liked this",
    "level": "Matches your level",
    "source": "Top result from a real source",
}


@dataclass(frozen=True)
class Signals:
    rating: str | None = None  # this user's own rating
    progress: str | None = None  # this user's own progress


NO_SIGNALS = Signals()


@dataclass(frozen=True)
class Part:
    key: str
    points: float


def score_parts(*, title, source, signals, level) -> list:
    parts = []
    if signals.rating == LIKE:
        parts.append(Part("own_like", W_OWN_LIKE))
    elif signals.rating == DISLIKE:
        parts.append(Part("own_dislike", W_OWN_DISLIKE))
    if signals.progress == DONE:
        parts.append(Part("own_done", W_OWN_DONE))
    elif signals.progress == SKIPPED:
        parts.append(Part("own_skipped", W_OWN_SKIPPED))

    if level in LEVEL_RULES:
        beginner_points, advanced_points = LEVEL_RULES[level]
        points = 0.0
        if BEGINNER_TITLE.search(title):
            points += beginner_points
        if ADVANCED_TITLE.search(title):
            points += advanced_points
        if points:
            parts.append(Part("level", points))

    trust = SOURCE_TRUST.get(source, 0.0)
    if trust:
        parts.append(Part("source", trust))
    return parts


def total(parts) -> float:
    return round(sum(part.points for part in parts), 6)


def is_hidden(signals) -> bool:
    """Things this person disliked or skipped are not shown to them again."""
    return signals.rating == DISLIKE or signals.progress == SKIPPED


def rank(resources, signals, level) -> list:
    """Best first, ties broken by id so the order is stable. Resources this person
    disliked or skipped are dropped completely, so rebuilding a plan never brings them
    back. Their absence can leave a step without links, but never a repeated link."""

    def signals_of(resource):
        return signals.get(resource.pk, NO_SIGNALS)

    def sort_key(resource):
        parts = score_parts(
            title=resource.title, source=resource.source, signals=signals_of(resource), level=level
        )
        return (-total(parts), resource.pk)

    return sorted((r for r in resources if not is_hidden(signals_of(r))), key=sort_key)


def reasons(parts) -> list:
    """Plain-language reasons. Only positive ones, and never any numbers, so one
    person's feedback can't be read back out of the text."""
    return [REASON_TEXT[p.key] for p in parts if p.points > 0 and p.key in REASON_TEXT]