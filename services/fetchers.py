"""Where learning resources come from. The rest of the app only knows this interface.
To use a real source later, add a class with `name` and `find(query)` and register it
in CLIENTS. Real clients must turn their own errors into FetchError."""
import json
import re
from dataclasses import dataclass
from urllib.error import URLError
from urllib.parse import quote, quote_plus, urlencode, urlsplit
from urllib.request import Request, urlopen

from django.conf import settings


class FetchError(Exception):
    pass


@dataclass(frozen=True)
class ResourceQuery:
    topic: str
    step_type: str  # "watch" | "read" | "practice"
    page: int = 0  # which page of results to ask the source for


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
    so the whole flow can be built and tested without any API key.

    `page` makes every page return a fresh, non-overlapping set, which is enough to
    exercise pagination/refill without touching the network."""

    name = "fake"

    def find(self, query: ResourceQuery) -> list[ResourceDraft]:
        t = query.topic
        page = max(int(getattr(query, "page", 0) or 0), 0)
        tag = "" if page == 0 else f" (part {page + 1})"
        extra = () if page == 0 else (f"part {page + 1}",)

        if query.step_type == "watch":
            return [
                ResourceDraft("video", f"Video: {t} for beginners{tag}", _search(YOUTUBE, t, "for beginners", *extra), self.name),
                ResourceDraft("video", f"Video: {t} full course{tag}", _search(YOUTUBE, t, "full course", *extra), self.name),
                ResourceDraft("video", f"Video: {t} explained simply{tag}", _search(YOUTUBE, t, "explained", *extra), self.name),
                ResourceDraft("video", f"Video: {t} crash course{tag}", _search(YOUTUBE, t, "crash course", *extra), self.name),
                ResourceDraft("video", f"Video: {t} tutorial for beginners{tag}", _search(YOUTUBE, t, "tutorial for beginners", *extra), self.name),
                ResourceDraft("video", f"Video: {t} complete guide{tag}", _search(YOUTUBE, t, "complete guide", *extra), self.name),
                ResourceDraft("video", f"Video: {t} full lecture{tag}", _search(YOUTUBE, t, "lecture", *extra), self.name),
                ResourceDraft("video", f"Video: {t} tips and tricks{tag}", _search(YOUTUBE, t, "tips and tricks", *extra), self.name),
            ]
        if query.step_type == "read":
            return [
                ResourceDraft("book", f"Books about {t}{tag}", _search(OPEN_LIBRARY, t, *extra), self.name),
                ResourceDraft("book", f"Introductory books on {t}{tag}", _search(OPEN_LIBRARY, t, "introduction", *extra), self.name),
                ResourceDraft("article", f"Wikipedia: {t}{tag}", _search(WIKIPEDIA, t, *extra), self.name),
                ResourceDraft("article", f"Beginner guides to {t}{tag}", _search(DUCKDUCKGO, t, "beginner guide", *extra), self.name),
                ResourceDraft("book", f"{t} handbook{tag}", _search(OPEN_LIBRARY, t, "handbook", *extra), self.name),
                ResourceDraft("article", f"Articles about {t}{tag}", _search(DUCKDUCKGO, t, "articles", *extra), self.name),
                ResourceDraft("article", f"{t} reading list{tag}", _search(DUCKDUCKGO, t, "reading list", *extra), self.name),
                ResourceDraft("article", f"{t} study guide{tag}", _search(DUCKDUCKGO, t, "study guide", *extra), self.name),
            ]
        if query.step_type == "practice":
            return [
                ResourceDraft("exercise", f"Exercises: {t}{tag}", _search(DUCKDUCKGO, t, "exercises", *extra), self.name),
                ResourceDraft("exercise", f"Practice problems: {t}{tag}", _search(DUCKDUCKGO, t, "practice problems", *extra), self.name),
                ResourceDraft("exercise", f"Project ideas: {t}{tag}", _search(DUCKDUCKGO, t, "project ideas", *extra), self.name),
                ResourceDraft("exercise", f"Quiz yourself: {t}{tag}", _search(DUCKDUCKGO, t, "quiz", *extra), self.name),
                ResourceDraft("exercise", f"Coding challenges: {t}{tag}", _search(DUCKDUCKGO, t, "challenges", *extra), self.name),
                ResourceDraft("exercise", f"Worksheets: {t}{tag}", _search(DUCKDUCKGO, t, "worksheets", *extra), self.name),
                ResourceDraft("exercise", f"Guided projects: {t}{tag}", _search(DUCKDUCKGO, t, "guided projects", *extra), self.name),
                ResourceDraft("exercise", f"Flashcards: {t}{tag}", _search(DUCKDUCKGO, t, "flashcards", *extra), self.name),
            ]
        raise FetchError(f"Unknown step type: {query.step_type!r}")


# --- Real sources ---------------------------------------------------------------

YOUTUBE_SEARCH = "https://www.googleapis.com/youtube/v3/search"
OPEN_LIBRARY_SEARCH = "https://openlibrary.org/search.json"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
GITHUB_SEARCH = "https://api.github.com/search/repositories"

# Query variants give each page new results for APIs that do not expose an offset.
YOUTUBE_VARIANTS = ("", "tutorial", "course", "explained", "lecture")
GITHUB_VARIANTS = ("exercises", "practice", "projects", "challenges", "tutorial")
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{5,20}")


def _get_json(url, params, *, headers=None) -> dict:
    """GET a JSON API. The API key, when one is needed, is passed as a header."""
    full_url = f"{url}?{urlencode(params)}"
    request = Request(
        full_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "LearnLoop/1.0 (+https://github.com/learnloop)",
            **(headers or {}),
        },
    )
    try:
        with urlopen(request, timeout=getattr(settings, "FETCH_TIMEOUT", 10)) as response:
            raw = response.read()
    except (URLError, OSError, TimeoutError) as exc:
        # Never include the query string in the message: some APIs put secrets there.
        raise FetchError(f"{urlsplit(url).netloc} request failed: {exc}") from exc

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"{urlsplit(url).netloc} returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise FetchError(f"{urlsplit(url).netloc} returned an unexpected response.")
    return data


class RealFetcher:
    """Fetches real resources from public APIs.

    - watch:    YouTube Data API (needs YOUTUBE_API_KEY)
    - read:     Open Library books + Wikipedia articles (no key)
    - practice: GitHub repository search (works without a token; GITHUB_TOKEN raises
                the rate limit)

    Each call asks for one `page`, so the caller can refill a plan without showing the
    same links again. If one read source is down the other still supplies resources.
    """

    name = "real"
    limit = 10

    def find(self, query: ResourceQuery) -> list[ResourceDraft]:
        page = max(int(getattr(query, "page", 0) or 0), 0)
        if query.step_type == "watch":
            drafts = self._youtube(query.topic, page)
        elif query.step_type == "read":
            drafts = self._read(query.topic, page)
        elif query.step_type == "practice":
            drafts = self._github(query.topic, page)
        else:
            raise FetchError(f"Unknown step type: {query.step_type!r}")

        seen, unique = set(), []
        for draft in drafts:
            if draft.url not in seen:
                seen.add(draft.url)
                unique.append(draft)
        return unique[: self.limit]

    def _youtube(self, topic, page):
        api_key = getattr(settings, "YOUTUBE_API_KEY", "")
        if not api_key:
            raise FetchError("YOUTUBE_API_KEY is not set, so watch resources cannot be fetched.")

        variant = YOUTUBE_VARIANTS[page % len(YOUTUBE_VARIANTS)]
        data = _get_json(
            YOUTUBE_SEARCH,
            {
                "part": "snippet",
                "type": "video",
                "q": f"{topic} {variant}".strip(),
                "maxResults": self.limit,
                "safeSearch": "moderate",
                "videoEmbeddable": "true",
            },
            headers={"X-Goog-Api-Key": api_key},
        )
        items = data.get("items")
        if not isinstance(items, list):
            raise FetchError("YouTube returned an unexpected response.")

        drafts = []
        for item in items:
            if not isinstance(item, dict):
                continue
            video_id = (item.get("id") or {}).get("videoId")
            title = (item.get("snippet") or {}).get("title")
            if (
                isinstance(video_id, str)
                and _VIDEO_ID.fullmatch(video_id)
                and isinstance(title, str)
                and title.strip()
            ):
                drafts.append(
                    ResourceDraft(
                        "video",
                        title,
                        f"https://www.youtube.com/watch?v={video_id}",
                        "youtube",
                    )
                )
        return drafts

    def _read(self, topic, page):
        half = max(1, self.limit // 2)
        drafts, errors = [], []
        for source, take in (
            (self._openlibrary, half),
            (self._wikipedia, self.limit - half),
        ):
            try:
                drafts.extend(source(topic, page, take))
            except FetchError as exc:
                errors.append(exc)
        if not drafts and errors:
            raise FetchError("; ".join(str(exc) for exc in errors))
        return drafts

    def _openlibrary(self, topic, page, limit):
        data = _get_json(
            OPEN_LIBRARY_SEARCH,
            {
                "q": topic,
                "page": page + 1,
                "limit": limit,
                "fields": "key,title,author_name",
            },
        )
        docs = data.get("docs")
        if not isinstance(docs, list):
            raise FetchError("Open Library returned an unexpected response.")

        drafts = []
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            title = doc.get("title")
            key = doc.get("key")
            if (
                isinstance(title, str)
                and title.strip()
                and isinstance(key, str)
                and key.startswith("/")
                and not key.startswith("//")
            ):
                drafts.append(
                    ResourceDraft(
                        "book",
                        title,
                        f"https://openlibrary.org{key}",
                        "openlibrary",
                    )
                )
        return drafts

    def _wikipedia(self, topic, page, limit):
        data = _get_json(
            WIKIPEDIA_API,
            {
                "action": "query",
                "list": "search",
                "srsearch": topic,
                "format": "json",
                "srlimit": limit,
                "sroffset": page * limit,
                "utf8": 1,
            },
        )
        query = data.get("query")
        results = query.get("search") if isinstance(query, dict) else None
        if not isinstance(results, list):
            raise FetchError("Wikipedia returned an unexpected response.")

        drafts = []
        for result in results:
            if not isinstance(result, dict):
                continue
            title = result.get("title")
            if isinstance(title, str) and title.strip():
                slug = quote(title.replace(" ", "_"), safe="")
                drafts.append(
                    ResourceDraft(
                        "article",
                        title,
                        f"https://en.wikipedia.org/wiki/{slug}",
                        "wikipedia",
                    )
                )
        return drafts

    def _github(self, topic, page):
        variant = GITHUB_VARIANTS[page % len(GITHUB_VARIANTS)]
        headers = {"X-GitHub-Api-Version": "2022-11-28"}
        token = getattr(settings, "GITHUB_TOKEN", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        data = _get_json(
            GITHUB_SEARCH,
            {
                "q": f"{topic} {variant}".strip(),
                "sort": "stars",
                "order": "desc",
                "per_page": self.limit,
                "page": page + 1,
            },
            headers=headers,
        )
        items = data.get("items")
        if not isinstance(items, list):
            raise FetchError("GitHub returned an unexpected response.")

        drafts = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = item.get("full_name") or item.get("name")
            url = item.get("html_url")
            if (
                isinstance(title, str)
                and title.strip()
                and isinstance(url, str)
                and url.startswith("https://github.com/")
            ):
                drafts.append(ResourceDraft("exercise", title, url, "github"))
        return drafts


CLIENTS = {"fake": FakeFetcher, "real": RealFetcher}


def get_fetcher():
    backend = getattr(settings, "RESOURCE_BACKEND", "fake")
    try:
        return CLIENTS[backend]()
    except KeyError:
        raise FetchError(f"Unknown resource backend: {backend!r}") from None
