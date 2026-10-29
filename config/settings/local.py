"""
Local development settings.

Loaded automatically when DJANGO_ENV is unset or "local"
(see config/settings/__init__.py).
"""

from .base import *  # noqa: F401,F403

DEBUG = True

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")

# Sensible defaults for local frontend dev servers; override via .env if needed
CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS",
    "http://127.0.0.1:5500,http://localhost:5173,http://localhost:3000,http://localhost:49181",
)

# Optional: run against local SQLite instead of MySQL by setting USE_SQLITE=True
if env_bool("USE_SQLITE", "False"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }