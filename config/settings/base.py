"""
Base settings shared across all environments (local, production, etc).

Environment-specific settings live in local.py and production.py, which both
start with `from .base import *`.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/topics/settings/
"""

import os
import sys
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env_list(key: str, default: str = "") -> list[str]:
    """Parse a comma-separated env var into a clean list of strings."""
    raw = os.getenv(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def env_bool(key: str, default: str = "False") -> bool:
    return os.getenv(key, default) == "True"


# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")

# ---------------------------------------------------------------------------
# Password pepper, versioned
# ---------------------------------------------------------------------------
# A secret mixed into every password before Django hashes it, kept here in
# the environment so a database dump alone is useless for cracking. It is
# deliberately NOT derived from SECRET_KEY: that key signs sessions, tokens
# and password-reset links, and rotating it must not re-key every password
# hash - nor the other way round. Numbered so it can be rotated:
#
#   PASSWORD_PEPPER_1=...
#   PASSWORD_PEPPER_2=...
#   PASSWORD_PEPPER_CURRENT_VERSION=2      (defaults to the highest number)
#
# New hashes use the current version; every user row records the version its
# hash was made with and is verified under that one, then re-hashed under the
# current one on the first successful sign-in after a rotation. Keep an old
# pepper until `manage.py pepper_status` reports no rows on it - remove it
# earlier and those accounts can only recover through a password reset.
#
# A pepper is required in every environment, local runs included. Version 0
# exists only so hashes from before the feature keep verifying - new hashes
# must never be minted bare, and a blank local .env once did exactly that,
# seeding accounts whose rows lied about their hash. See core/pepper.py for
# the mixing itself.


def _password_peppers_from_env() -> dict[int, str]:
    peppers = {}
    prefix = "PASSWORD_PEPPER_"
    for name, value in os.environ.items():
        suffix = name[len(prefix):]
        if not name.startswith(prefix) or not suffix.isdigit() or not value:
            continue
        version = int(suffix)
        if version == 0:
            raise ImproperlyConfigured(
                "PASSWORD_PEPPER_0 is not allowed: version 0 means 'no pepper'."
            )
        peppers[version] = value
    return dict(sorted(peppers.items()))


PASSWORD_PEPPERS = _password_peppers_from_env()

if not PASSWORD_PEPPERS:
    raise ImproperlyConfigured(
        "No PASSWORD_PEPPER_<N> is set. Generate one with "
        "`python -c \"import secrets; print(secrets.token_urlsafe(48))\"` "
        "and put it in the environment as PASSWORD_PEPPER_1."
    )

_pepper_current = os.getenv("PASSWORD_PEPPER_CURRENT_VERSION", "").strip()
if _pepper_current.isdigit() and int(_pepper_current) in PASSWORD_PEPPERS:
    PASSWORD_PEPPER_CURRENT_VERSION = int(_pepper_current)
elif _pepper_current.isdigit():
    raise ImproperlyConfigured(
        f"PASSWORD_PEPPER_CURRENT_VERSION={_pepper_current} but no "
        f"PASSWORD_PEPPER_{_pepper_current} is set."
    )
else:
    PASSWORD_PEPPER_CURRENT_VERSION = max(PASSWORD_PEPPERS)

# Real value is set per-environment in local.py / production.py
DEBUG = False

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Number formatting in the report PDF template (intcomma)
    "django.contrib.humanize",
    # Project apps
    #
    # `api.rbac` first: it owns the role catalog and the permission holders
    # for endpoints that have no model of their own, and `seed_rbac` reads it.
    "api.rbac",
    # Owns `seed_all` / `bootstrap` and the SeedCommand base class; the
    # per-feature seeders live in the apps they seed.
    "api.seeding",
    "api.schools",
    "api.institutes",
    "api.programs",
    "api.students",
    "api.users",
    "api.clearance",
    "api.notifications",
    "api.system_settings.apps.SystemSettingsConfig",
    "api.audits",
    "api.reports",
    "api.health",
    "api.change_password",
    "api.change_email",
    # Third-party
    "rest_framework",
    "drf_spectacular",
    "django_filters",
    "corsheaders",
    "cloudinary",
    "cloudinary_storage",
    "rest_framework_simplejwt.token_blacklist",
    "django_extensions",
]

MIDDLEWARE = [
    # 1. MUST be at the very top to catch all incoming requests/preflights first
    "corsheaders.middleware.CorsMiddleware",
    
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # Best practice placement for WhiteNoise
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "core"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.getenv("DB_NAME"),
        "USER": os.getenv("DB_USER"),
        "PASSWORD": os.getenv("DB_PASSWORD"),
        "HOST": os.getenv("DB_HOST"),
        "PORT": os.getenv("DB_PORT"),
    }
}

# Cache
#
# Redis when REDIS_URL is set, Django's built-in in-memory cache when it is
# not. Set the variable and everything switches over - no code changes, no
# migration, nothing to run. Unset it and you are back to the fallback.
#
#     REDIS_URL=redis://127.0.0.1:6379/1          local
#     REDIS_URL=rediss://user:pass@host:port/0    managed, TLS
#
# The one thing that actually differs between the two is whether the cache is
# shared between worker processes:
#
#   Redis   - one cache for every worker. A record edit invalidates it for
#             everybody, immediately. This is the mode the caching in
#             `api/fees/cache.py` was designed for.
#
#   LocMem  - one cache per worker process. Worker A invalidating its own
#             copy does not touch worker B's, so a stale entry can survive on
#             another worker until it times out. That window is why the
#             fallback runs a much shorter TTL (below): correctness comes from
#             invalidation when Redis is on, and from the short TTL when it is
#             not.
#
# Redis needs the `redis` package (in requirements.txt); the cache backend
# itself ships with Django, so there is nothing else to install.
REDIS_URL = os.getenv("REDIS_URL")

# True when every worker shares one cache. Read this rather than re-checking
# REDIS_URL if you ever need to branch on it.
CACHE_IS_SHARED = bool(REDIS_URL)

# How long a cached value may live. On Redis this is only a backstop - an
# invalidation has already removed anything stale. On the fallback it is also
# the ceiling on how long another worker can serve an out-of-date page, so it
# is deliberately short.
CACHE_DEFAULT_TTL = 60 * 30 if CACHE_IS_SHARED else 60

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "TIMEOUT": CACHE_DEFAULT_TTL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "dnsc-systems-api",
            "TIMEOUT": CACHE_DEFAULT_TTL,
            # Django's default cap is 300 entries, which one paged fee list
            # can fill on its own and evict everything else with.
            "OPTIONS": {"MAX_ENTRIES": 5000, "CULL_FREQUENCY": 4},
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTH_USER_MODEL = "users.User"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")

# --- CORS & CSRF SETTINGS ---
# MUST be explicitly set to True when frontend uses credentials/cookies/headers
CORS_ALLOW_CREDENTIALS = True

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": "2000/hour" if os.getenv("DJANGO_ENV") == "local" else "100000/hour",
        "anon": "10/min",
        "login_account": "3/m",
        "login_ip": "10/m",
        "transfer_token_issue": "20/min",
        "transfer_token_redeem": "20/min",
        # Email change. Sending mail is the abusable part, so the two
        # send-y steps share one per-user budget - loose enough to mistype
        # the new address a couple of times and start over. Code entry has
        # its own; the request row also locks itself after 5 wrong codes.
        "email_change_request": "10/hour",
        "email_change_verify": "20/hour",
        # Per IP, and a whole campus can sit behind one. The link token is
        # 256 bits, so this is DoS protection, not anti-guessing - it can
        # afford to be generous rather than refuse a real confirmation.
        "email_change_confirm": "120/hour",
    },
    "DEFAULT_RENDERER_CLASSES": ("api.utils.renderer.CustomResponseRenderer",),
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=int(os.getenv("JWT_ACCESS_TOKEN_LIFETIME_MINUTES", "15"))
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(
        days=int(os.getenv("JWT_REFRESH_TOKEN_LIFETIME_DAYS", "31"))
    ),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": os.getenv("JWT_SIGNING_KEY"),
}

SPECTACULAR_SETTINGS = {
    "TITLE": os.getenv("API_TITLE", "E-CLEARANCE SYSTEM API"),
    "DESCRIPTION": os.getenv(
        "API_DESCRIPTION", "Institute of Computing E-Clearance System API"
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,  # hide raw schema from swagger
    "CONTACT": {
        "email": os.getenv("API_CONTACT_EMAIL", "froydcarbajosa14@gmail.com"),
    },
    "LICENSE": {"name": "MIT"},
    "ENUM_NAME_OVERRIDES": {
        "ProgramStatusEnum": "api.programs.models.Program.ProgramStatus",
        "StudentStatusEnum": "api.students.models.Student.StudentStatus",
        "StudentClearanceStatusEnum": "api.clearance.models.StudentClearance.ClearanceStatus",
        "StudentClearanceItemStatusEnum": "api.clearance.models.StudentClearanceItem.ItemStatus",
        "AcademicYearEnum": "core.choices.AcademicYear.choices",
    },
}

MEDIA_URL = "/media/"
DEFAULT_FILE_STORAGE = "cloudinary_storage.storage.MediaCloudinaryStorage"

# ---------------------------------------------------------------------------
# Cloudinary accounts, versioned
# ---------------------------------------------------------------------------
# Free-tier storage runs out. Rather than migrate every image when it does,
# a deployment adds an account and points NEW uploads at it while every
# image already uploaded stays on the account that holds it. Each account is
# a numbered block of environment variables:
#
#   CLOUDINARY_CLOUD_NAME_1 / CLOUDINARY_API_KEY_1 / CLOUDINARY_API_SECRET_1
#   CLOUDINARY_CLOUD_NAME_2 / ...
#   CLOUDINARY_CURRENT_VERSION=2        (defaults to the highest block)
#
# `CLOUDINARY_ACCOUNTS` maps the version number to the credentials, the
# current version is what `api/utils/cloudinary/cloudinary_utils.py` uploads
# to, and every model row that stores a public id also stores the version it
# was uploaded under, so reads and deletes always name the right account -
# the same shape as a versioned password pepper. Adding an account is a
# matter of environment variables only: no code changes.
#
# The original un-numbered trio (CLOUDINARY_CLOUD_NAME and friends) is still
# honoured as version 1 so an existing deployment keeps working untouched.


def _cloudinary_accounts_from_env() -> dict:
    accounts = {}
    prefix = "CLOUDINARY_CLOUD_NAME_"
    for name, cloud_name in os.environ.items():
        if not name.startswith(prefix) or not cloud_name:
            continue
        suffix = name[len(prefix):]
        if not suffix.isdigit():
            continue
        version = int(suffix)
        accounts[version] = {
            "cloud_name": cloud_name,
            "api_key": os.getenv(f"CLOUDINARY_API_KEY_{version}", ""),
            "api_secret": os.getenv(f"CLOUDINARY_API_SECRET_{version}", ""),
        }
    if 1 not in accounts and os.getenv("CLOUDINARY_CLOUD_NAME"):
        accounts[1] = {
            "cloud_name": os.getenv("CLOUDINARY_CLOUD_NAME"),
            "api_key": os.getenv("CLOUDINARY_API_KEY", ""),
            "api_secret": os.getenv("CLOUDINARY_API_SECRET", ""),
        }
    return dict(sorted(accounts.items()))


CLOUDINARY_ACCOUNTS = _cloudinary_accounts_from_env()

_current = os.getenv("CLOUDINARY_CURRENT_VERSION", "").strip()
if _current.isdigit() and int(_current) in CLOUDINARY_ACCOUNTS:
    CLOUDINARY_CURRENT_VERSION = int(_current)
elif _current.isdigit():
    raise ImproperlyConfigured(
        f"CLOUDINARY_CURRENT_VERSION={_current} but no CLOUDINARY_CLOUD_NAME_{_current} is set."
    )
else:
    CLOUDINARY_CURRENT_VERSION = max(CLOUDINARY_ACCOUNTS) if CLOUDINARY_ACCOUNTS else 1

# Bulk deletes by folder prefix (`cloudinary_utils.delete_by_prefix`, run
# when a school or institute is destroyed) are switched OFF under the test
# runner. The test database reuses ids 1, 2, 3 and the credentials come from
# the same .env as everything else, so an un-mocked destroy test would wipe
# the real `schools/1/` folder. A test that wants to see the call patches it.
CLOUDINARY_PREFIX_DELETE_ENABLED = sys.argv[1:2] != ["test"]

# django-cloudinary-storage (DEFAULT_FILE_STORAGE) and the SDK's global
# configuration follow the CURRENT account. Anything that touches an
# existing asset goes through cloudinary_utils with the row's own version.
_current_account = CLOUDINARY_ACCOUNTS.get(CLOUDINARY_CURRENT_VERSION, {})
CLOUDINARY_STORAGE = {
    "CLOUD_NAME": _current_account.get("cloud_name"),
    "API_KEY": _current_account.get("api_key"),
    "API_SECRET": _current_account.get("api_secret"),
}

# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------
# Optional modules this deployment runs. See `core/features.py` for what each
# one covers and `.env.example` for the variables. A flag that is off is off
# everywhere: the routes are not registered, the permissions are not granted,
# and `/api/v1/me/` reports it so the frontend hides the whole area.
FEATURES = {
    "contributions": env_bool("FEATURE_CONTRIBUTIONS", "True"),
    "contribution_points": env_bool("FEATURE_CONTRIBUTION_POINTS", "True"),
    "complaints": env_bool("FEATURE_COMPLAINTS", "True"),
    # Off by default: a deployment opts in to handing out money.
    "coupons": env_bool("FEATURE_COUPONS", "False"),
}

# ---------------------------------------------------------------------------
# Live systems
# ---------------------------------------------------------------------------
# Which of the systems in `core.choices.SystemScope` have a frontend that
# actually reads their settings. `seed_system_settings` writes maintenance
# switches only for these, for the same reason `FEATURES` gates the module
# rows: a switch for an app nobody has built does nothing when somebody flips
# it, and there is no way to tell that from the settings page.
#
# The day the locker app ships, this gains a word and the next seed writes its
# rows. `seed_system_settings --prune` removes the rows for a system taken
# back off the list.
LIVE_SYSTEMS = tuple(
    system.strip()
    for system in os.getenv("LIVE_SYSTEMS", "collection").split(",")
    if system.strip()
)


FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

# The public base URL of THIS API, and the one thing every emailed link is
# built from (see core/services/links.py). Point it at localhost and a
# locally-triggered invitation lands on your local server; point it at the
# deployed host and it lands there. There used to be a second
# BACKEND_DEPLOYED_URL alongside it, which meant every link had to pick one
# and local testing got production URLs - production.py now asserts this one
# is really set instead.
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")

# Email
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", "True")
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", "False")
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER)
EMAIL_TIMEOUT = 20
# Send transactional mail on a background thread. Off makes every send
# synchronous - what the test suite needs, and useful when debugging SMTP.
EMAIL_SEND_ASYNC = env_bool("EMAIL_SEND_ASYNC", "True")

# Who transactional email says it is from. The school of the account the mail
# concerns wins; this is the fallback for accounts with no school attached
# (see core/services/email_branding.py).
EMAIL_BRAND_NAME = os.getenv("EMAIL_BRAND_NAME", "Collection Management System")
# Accent colour used by the email templates and the pages their links land on.
EMAIL_BRAND_COLOR = os.getenv("EMAIL_BRAND_COLOR", "#04932a")
# Shown as the "need help?" address in email footers; falls back to the sender.
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "")
# The logo in the email header when the mail belongs to no institute and no
# school - a superuser's own mail, a reset for an unattached account. Tenant
# mail uses the institute's Cloudinary logo, then the school's.
EMAIL_BRAND_LOGO_URL = os.getenv("EMAIL_BRAND_LOGO_URL", "")

# --- Per-school / per-institute senders -------------------------------------
# A school or institute can mail through its own Gmail account (address +
# app password, set from the console). The app password is stored encrypted
# under this key - see core/email_secrets.py. Comma-separated to rotate:
# the first key encrypts, every key decrypts. Blank locally derives a key
# from DJANGO_SECRET_KEY; production.py refuses that when IS_DEPLOYED=True.
EMAIL_SECRETS_KEYS = tuple(
    key.strip() for key in os.getenv("EMAIL_SECRETS_KEY", "").split(",") if key.strip()
)

# --- Email change (api/change_email) ---------------------------------------
# Step 1 mails a code to the CURRENT address; step 3 mails a link to the NEW
# one. Nothing is written to User.email until that link is opened.
EMAIL_CHANGE_OTP_LENGTH = int(os.getenv("EMAIL_CHANGE_OTP_LENGTH", "6"))
EMAIL_CHANGE_OTP_EXPIRY_MINUTES = int(
    os.getenv("EMAIL_CHANGE_OTP_EXPIRY_MINUTES", "10")
)
EMAIL_CHANGE_TOKEN_EXPIRY_MINUTES = int(
    os.getenv("EMAIL_CHANGE_TOKEN_EXPIRY_MINUTES", "15")
)
# Longer than the others on purpose: the recipient may have to go and sign in
# to an inbox they do not have open.
EMAIL_CHANGE_CONFIRM_EXPIRY_MINUTES = int(
    os.getenv("EMAIL_CHANGE_CONFIRM_EXPIRY_MINUTES", "60")
)
EMAIL_CHANGE_MAX_ATTEMPTS = int(os.getenv("EMAIL_CHANGE_MAX_ATTEMPTS", "5"))

# --- Invitations (api/users) ------------------------------------------------
# Invited accounts are created with a one-time password, mailed with the
# invitation, so a new user can sign in without a password-reset round trip.
# Off means the account is created with no usable password and the user must
# use "forgot password" instead.
INVITE_SEND_TEMPORARY_PASSWORD = env_bool("INVITE_SEND_TEMPORARY_PASSWORD", "True")
INVITE_TEMPORARY_PASSWORD_LENGTH = int(
    os.getenv("INVITE_TEMPORARY_PASSWORD_LENGTH", "12")
)
INVITE_EXPIRY_DAYS = int(os.getenv("INVITE_EXPIRY_DAYS", "3"))


# --- health app specific settings, all optional ---
# Which checks exist/run at all (default: every built-in check)
# HEALTH_CHECKS = ["database", "cache"]

# Which of those checks must pass for /health/ready/ to return 200
# HEALTH_CHECK_READINESS =["database", "cache"]

# Only relevant if you keep the disk check enabled
HEALTH_CHECK_DISK_MIN_PERCENT = 20

# Only relevant if you keep the DNS check enabled
HEALTH_CHECK_DNS_HOST = "api.instituteofcomputing.org"

# Shown in the /health/ detail payload
HEALTH_CHECK_VERSION = "1.4.2"
HEALTH_CHECK_ENVIRONMENT = "production"


# for password reset feature
PASSWORD_RESET_OTP_LENGTH = 6
PASSWORD_RESET_OTP_EXPIRY_MINUTES = 10
PASSWORD_RESET_CHANGE_TOKEN_EXPIRY_MINUTES = 10
PASSWORD_RESET_MAX_ATTEMPTS = 5