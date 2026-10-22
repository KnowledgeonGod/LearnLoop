import os
import sys
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Tests use an in-memory SQLite database by default so they never touch the
# hosted database in .env. Set DATABASE_URL in the shell to opt into another
# test database.
if "test" in sys.argv:
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

load_dotenv(BASE_DIR / ".env")

# Secrets management: from the environment, never hardcoded
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = os.environ.get("DJANGO_DEBUG") == "1"
ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h]


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "axes",
    "apps.accounts",
    "apps.topics",
    "apps.quiz",
    "apps.learning",
    "apps.resources",
    "apps.feedback",

]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",  # keep last
]
ROOT_URLCONF = 'myproject.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'myproject.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases
#
# DATABASE_URL takes precedence so hosted Postgres providers (Neon, Supabase,
# Railway, ...) only need one secret. Without it, DB_ENGINE=postgres uses the
# individual DB_* settings; otherwise SQLite is the zero-configuration default.
DB_ENGINE = os.environ.get("DB_ENGINE", "sqlite").strip().lower()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DB_CONN_MAX_AGE = int(os.environ.get("DB_CONN_MAX_AGE", "60"))

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=DB_CONN_MAX_AGE,
            conn_health_checks=True,
        )
    }
    # PgBouncer-style poolers (including Neon's -pooler hosts) do not support
    # server-side cursors in transaction pooling mode.
    if "-pooler." in (DATABASES["default"].get("HOST") or ""):
        DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
elif DB_ENGINE in {"postgres", "postgresql"}:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "learnloop"),
            "USER": os.environ.get("DB_USER", ""),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
            "PORT": os.environ.get("DB_PORT", "5432"),
            "CONN_MAX_AGE": DB_CONN_MAX_AGE,
        }
    }
else:
    _db_name = os.environ.get("DB_NAME", "")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": Path(_db_name) if _db_name else BASE_DIR / "db.sqlite3",
        }
    }


# Password validation
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1 
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

# Which client builds learning plans: "fake" (offline, deterministic) or "openai"
# (OpenAI-compatible chat completions, including local servers).
LLM_BACKEND = os.environ.get("LLM_BACKEND", "fake")
LLM_API_URL = os.environ.get("LLM_API_URL", "https://api.openai.com/v1/chat/completions")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "30"))
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "1200"))
# Transient provider failures (rate limits, 5xx) are retried with backoff.
LLM_RETRIES = int(os.environ.get("LLM_RETRIES", "2"))
LLM_RETRY_DELAY = float(os.environ.get("LLM_RETRY_DELAY", "1.5"))

# Which source provides resources: "fake" (offline) or "real" (YouTube, Open
# Library, Wikipedia, Google Books and GitHub). YouTube needs YOUTUBE_API_KEY;
# GitHub works without a token but has a lower rate limit.
RESOURCE_BACKEND = os.environ.get("RESOURCE_BACKEND", "fake")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
FETCH_TIMEOUT = int(os.environ.get("FETCH_TIMEOUT", "10"))

# Background plan generation. Disabled by default so a fresh clone works with a
# single process; enable it and run `python manage.py run_plan_worker` to keep the
# browser request fast when real providers take 30-90 seconds.
PLAN_JOBS_ENABLED = os.environ.get("PLAN_JOBS_ENABLED", "0") == "1"
PLAN_JOB_POLL_SECONDS = float(os.environ.get("PLAN_JOB_POLL_SECONDS", "2"))
# After this many seconds in QUEUED, the progress page warns that the worker may be down.
PLAN_JOB_WORKER_HINT_SECONDS = int(os.environ.get("PLAN_JOB_WORKER_HINT_SECONDS", "10"))
# A worker that dies while a job is RUNNING would otherwise strand it forever.
PLAN_JOB_STALE_MINUTES = int(os.environ.get("PLAN_JOB_STALE_MINUTES", "30"))

# Cost controls. 0 means unlimited; 20 is a sensible paid-provider default.
PLAN_DAILY_LIMIT = int(os.environ.get("PLAN_DAILY_LIMIT", "0"))
LLM_USAGE_TRACKING = os.environ.get("LLM_USAGE_TRACKING", "1") == "1"

# Keep tests offline even when .env selects the real backends.
TEST_RUNNER = "myproject.test_runner.OfflineBackendsTestRunner"

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'


# Logging: one console handler, no secrets. Set DJANGO_LOG_LEVEL=DEBUG for more detail.
LOG_LEVEL = os.environ.get("DJANGO_LOG_LEVEL", "INFO").upper()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "apps": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "services": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}


# Email
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration

MAILERS = {
    'default': {
        'BACKEND': 'django.core.mail.backends.console.EmailBackend',
    },
}

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# Production security. These can be overridden for a local HTTPS test.
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]
SECURE_SSL_REDIRECT = os.environ.get(
    "DJANGO_SECURE_SSL_REDIRECT", "0" if DEBUG else "1"
) == "1"
SECURE_HSTS_SECONDS = int(
    os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "0" if DEBUG else "31536000")
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
if os.environ.get("DJANGO_TRUST_X_FORWARDED_PROTO", "0") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
