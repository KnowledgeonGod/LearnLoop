# Deployment

LearnLoop is a standard Django application. This guide describes a small
single-server deployment; a container platform works the same way.

## 1. Install dependencies

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`requirements.txt` includes Waitress, a Windows-friendly production WSGI server. For
PostgreSQL, install the driver in the same environment:

```powershell
pip install "psycopg[binary]"
```

## 2. Configure environment

Copy `.env.example` to `.env` on the server and set real values. Never commit `.env`.

Minimum production settings:

```dotenv
DJANGO_SECRET_KEY=<long-random-secret>
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=learnloop.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://learnloop.example.com
DJANGO_SECURE_SSL_REDIRECT=1
DJANGO_TRUST_X_FORWARDED_PROTO=1
DJANGO_LOG_LEVEL=INFO

DB_ENGINE=postgres
DB_NAME=learnloop
DB_USER=learnloop
DB_PASSWORD=<database-password>
DB_HOST=127.0.0.1
DB_PORT=5432
DB_CONN_MAX_AGE=60

PLAN_JOBS_ENABLED=1
PLAN_DAILY_LIMIT=20
LLM_USAGE_TRACKING=1
```

Keep `DJANGO_DEBUG=0`. In that mode secure cookies, HSTS and SSL redirect are enabled by
default. If TLS terminates at a reverse proxy, set
`DJANGO_TRUST_X_FORWARDED_PROTO=1` so Django knows the original request was HTTPS.

## 3. Prepare the database and static files

```powershell
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py collectstatic --noinput
.\venv\Scripts\python.exe manage.py check --deploy
```

`check --deploy` may warn about `SECRET_KEY` length or HSTS; fix those in `.env` before
going live. `STATIC_ROOT` is `staticfiles/`; point the reverse proxy at it or use
WhiteNoise if you prefer serving static files from Django.

## 4. Start the web process

```powershell
.\venv\Scripts\waitress-serve.exe --listen=127.0.0.1:8000 myproject.wsgi:application
```

Put Nginx, IIS or another reverse proxy in front of it, terminate HTTPS there, and
forward `X-Forwarded-Proto: https`.

## 5. Start the plan worker

Background generation needs one continuously running worker:

```powershell
.\venv\Scripts\python.exe manage.py run_plan_worker
```

Use a process supervisor (Windows Service, NSSM, systemd, or your platform's worker
type) so it restarts automatically. The worker requeues jobs left `RUNNING` for more
than `PLAN_JOB_STALE_MINUTES` (default 30). For a cron-style setup, run
`run_plan_worker --once` every minute instead of keeping a worker alive.

If `PLAN_JOBS_ENABLED=0`, generation runs inside the web request and no worker is
needed, but a slow provider can tie up a web process.

## 6. Health checks and logs

- Load balancer check: `GET /healthz/` (HTTP 200 healthy, 503 database failure).
- Application logs go to the process console; collect them with your supervisor.
- `DJANGO_LOG_LEVEL` controls verbosity. Never log request bodies, prompts or keys.

## 7. Periodic maintenance

```powershell
# Remove old unlinked resources and stale pagination cursors.
.\venv\Scripts\python.exe manage.py prune_resources --days 30

# Print recent LLM token and resource-fetch usage.
.\venv\Scripts\python.exe manage.py usage_report --days 7
```

Schedule these daily or weekly. `prune_resources` preserves resources that are still
linked to a plan, so existing plans keep their links.

## 8. Backups

Back up the database (`pg_dump` for PostgreSQL, or the SQLite file) and the `.env`
secret store. Static files can be regenerated with `collectstatic`.
