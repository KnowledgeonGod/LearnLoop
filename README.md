# LearnLoop

LearnLoop turns "I want to learn X" into a personalized, step-by-step learning plan
with real resource links.

A learner signs up, searches a topic, answers a short quiz, and gets a plan. Each step
links to resources (videos, books, articles, exercises). Feedback — like, dislike,
done, skip — is remembered per user and reshapes the next rebuild. Plans and feedback
are private: one learner's feedback never changes what another learner sees.

## Quick start

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
# edit .env and set DJANGO_SECRET_KEY
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py runserver
```

Run the tests with:

```powershell
.\venv\Scripts\python.exe manage.py test
```

## Backends

Both backends default to deterministic, offline fakes, so the app works without any
API keys.

### Plans: `LLM_BACKEND`

| Value | Behaviour |
|---|---|
| `fake` (default) | Deterministic plans, no network. |
| `openai` | Any OpenAI-compatible chat-completions endpoint. |

```dotenv
LLM_BACKEND=openai
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
# Optional. Point this at a local Ollama/vLLM server, for example:
# LLM_API_URL=http://localhost:11434/v1/chat/completions
# LLM_TIMEOUT=30
# LLM_MAX_TOKENS=1200
```

The model is asked for strict JSON, but the parser also tolerates Markdown fences and
surrounding prose. Every field is still validated by the app before it is saved.

Any OpenAI-compatible endpoint works. For example, Google Gemini's free tier:

```dotenv
LLM_BACKEND=openai
LLM_API_URL=https://generativelanguage.googleapis.com/v1beta/openai/chat/completions
LLM_API_KEY=your-gemini-key
LLM_MODEL=gemini-3.8-flash
LLM_MAX_TOKENS=8192
LLM_TIMEOUT=120
```

Reasoning models spend output tokens on internal thinking, so they need a larger
`LLM_MAX_TOKENS` and `LLM_TIMEOUT` than the defaults.

### Resources: `RESOURCE_BACKEND`

| Value | Behaviour |
|---|---|
| `fake` (default) | Offline search links, 8 per page. |
| `real` | YouTube, Open Library, Wikipedia and GitHub. |

```dotenv
RESOURCE_BACKEND=real
YOUTUBE_API_KEY=...
# Optional; raises the GitHub search rate limit.
GITHUB_TOKEN=...
# FETCH_TIMEOUT=10
```

Where the resources come from:

- **Watch** — YouTube Data API v3 (requires `YOUTUBE_API_KEY`).
- **Read** — Open Library books + Wikipedia articles (no key needed).
- **Practice** — GitHub repository search (works without a token).

If one read source is down, the other still supplies links. No resource is repeated
inside a plan.

### Check your setup

After editing `.env`, run:

```powershell
.\venv\Scripts\python.exe manage.py check_backends
```

It prints the selected backends, whether each required key is present, and exits with
code 1 when a required key is missing. It never prints a secret value. Add `--test` to
make one small live request to each configured backend (this can use a few LLM tokens):

```powershell
.\venv\Scripts\python.exe manage.py check_backends --test
```

Restart `runserver` after changing `.env`.

### Refill and pagination

Each topic/step type remembers which pages it has already fetched. When feedback
hides most of the cached resources, a rebuild asks the source for the next page instead
of re-showing hidden links. A cap (`MAX_FETCH_PAGES` in `apps/resources/services.py`)
prevents endless calls when a source is genuinely exhausted. The cache is shared across
users and refreshed after seven days.

If every resource in a section is hidden, the section is removed from the plan page.
Hidden resources stay in a collapsed panel so a dislike or skip can be undone.

## Troubleshooting

### "We couldn't build your plan right now"

1. Run `.\venv\Scripts\python.exe manage.py check_backends`.
2. If it reports `api key: MISSING`, open `.env` (not `.env.example`, which is only
   the tracked template) and either add a real key:

   ```dotenv
   LLM_BACKEND=openai
   LLM_API_KEY=sk-...
   ```

   or use the offline planner while you get one:

   ```dotenv
   LLM_BACKEND=fake
   ```

3. If it reports `YouTube key: MISSING`, watch steps will have no resources. Add
   `YOUTUBE_API_KEY=...`, or set `RESOURCE_BACKEND=fake` to use offline search links.
4. If `check_backends --test` reports `no credits remaining`, add credits to the
   provider account, or keep `LLM_BACKEND=fake`.
5. If it reports `ran out of output tokens`, raise `LLM_MAX_TOKENS` (for example 8192)
   and `LLM_TIMEOUT` (for example 120). This is common with reasoning models.
6. A transient HTTP 5xx or rate limit is retried automatically; if it still fails,
   wait a moment and try again.
7. Restart `runserver`, then try the plan again.

The plan page shows the configuration message directly when a backend is selected but
its key is empty. Other provider errors stay generic on purpose; the full reason is in
the server log.

## Project layout

```
apps/accounts    users, profile, login/signup
apps/topics      topics, search history, dashboard
apps/quiz        the personalization quiz
apps/learning    plan generation, validation, plan page
apps/resources   resource catalog, fetching, caching, ranking hooks
apps/feedback    like/dislike/done/skip and undo
services/llm.py       the LLM boundary and clients
services/fetchers.py  the resource-source boundary and clients
services/ranking.py   pure ranking rules
```

## Security notes

- Plans and feedback are always looked up through `request.user`.
- Third-party text and URLs are validated before storage and escaped in templates.
- Only `http`/`https` resource URLs are stored.
- API keys live in `.env` (gitignored) and are sent in headers, never query strings.
- Signup, login and plan generation are rate-limited.
