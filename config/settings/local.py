"""
Local development settings.

Loaded automatically when DJANGO_ENV is unset or "local"
(see config/settings/__init__.py).
"""

from .base import *  # noqa: F401,F403

DEBUG = True

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")

# Sensible defaults for local frontend dev servers; override via .env if needed
_local_cors = env_list(
    "CORS_ALLOWED_ORIGINS",
    "http://127.0.0.1:5500,http://localhost:5173,http://localhost:3000,http://localhost:49181,https://e-clearance-dnsc.vercel.app",
)
CORS_ALLOWED_ORIGINS = sorted(
    list(
        {
            origin.rstrip("/")
            for origin in (CORS_ALLOWED_ORIGINS + _local_cors)
            if origin.strip()
        }
    )
)

# Optional: run against local SQLite instead of MySQL by setting USE_SQLITE=True
if env_bool("USE_SQLITE", "False"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }