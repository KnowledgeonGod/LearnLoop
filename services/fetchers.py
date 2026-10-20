"""Where learning resources come from. The rest of the app only knows this interface.
To use a real source later, add a class with `name` and `find(query)` and register it
in CLIENTS. Real clients must turn their own errors into FetchError."""
from dataclasses import dataclass
from urllib.parse import quote_plus

from django.conf import settings


class FetchError(Exception):
    pass


@dataclass(frozen=True)
class ResourceQuery:
    topic: str
    step_type: str  # "watch" | "read" | "practice"


@dataclass(frozen=True)
class ResourceDraft:
    kind: str  # "video" | "book" | "article" | "exercise"
    title: str
    url: str
    source: str


def _search(base: str, *parts: str) -> str:
    # quote_plus encodes &, #, spaces and non-Latin text, so topic text can never
    # inject extra URL parameters or a fragment.
    return base + quote_plus(" ".join(parts))


YOUTUBE = "https://www.youtube.com/results?search_query="
OPEN_LIBRARY = "https://openlibrary.org/search?q="
WIKIPEDIA = "https://en.wikipedia.org/w/index.php?search="
DUCKDUCKGO = "https://duckduckgo.com/?q="


class FakeFetcher:
    """Deterministic and offline. It returns working SEARCH links, not curated picks,
    so the whole flow can be built and tested without any API key."""

    name = "fake"

    def find(self, query: ResourceQuery) -> list[ResourceDraft]:
        t = query.topic
        if query.step_type == "watch":
            return [
                ResourceDraft("video", f"Video: {t} for beginners", _search(YOUTUBE, t, "for beginners"), self.name),
                ResourceDraft("video", f"Video: {t} full course", _search(YOUTUBE, t, "full course"), self.name),
                ResourceDraft("video", f"Video: {t} explained simply", _search(YOUTUBE, t, "explained"), self.name),
                ResourceDraft("video", f"Video: {t} crash course", _search(YOUTUBE, t, "crash course"), self.name),
            ]
        if query.step_type == "read":
            return [
                ResourceDraft("book", f"Books about {t}", _search(OPEN_LIBRARY, t), self.name),
                ResourceDraft("book", f"Introductory books on {t}", _search(OPEN_LIBRARY, t, "introduction"), self.name),
                ResourceDraft("article", f"Wikipedia: {t}", _search(WIKIPEDIA, t), self.name),
                ResourceDraft("article", f"Beginner guides to {t}", _search(DUCKDUCKGO, t, "beginner guide"), self.name),
            ]
        if query.step_type == "practice":
            return [
                ResourceDraft("exercise", f"Exercises: {t}", _search(DUCKDUCKGO, t, "exercises"), self.name),
                ResourceDraft("exercise", f"Practice problems: {t}", _search(DUCKDUCKGO, t, "practice problems"), self.name),
                ResourceDraft("exercise", f"Project ideas: {t}", _search(DUCKDUCKGO, t, "project ideas"), self.name),
                ResourceDraft("exercise", f"Quiz yourself: {t}", _search(DUCKDUCKGO, t, "quiz"), self.name),
            ]
        raise FetchError(f"Unknown step type: {query.step_type!r}")


CLIENTS = {"fake": FakeFetcher}


def get_fetcher():
    backend = getattr(settings, "RESOURCE_BACKEND", "fake")
    try:
        return CLIENTS[backend]()
    except KeyError:
        raise FetchError(f"Unknown resource backend: {backend!r}") from None