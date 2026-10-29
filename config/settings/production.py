"""
Production settings.

Loaded automatically when DJANGO_ENV=production
(see config/settings/__init__.py).
"""

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403

DEBUG = False

_raw_allowed = env_list(
    "DJANGO_ALLOWED_HOSTS",
    "localhost,127.0.0.1,.onrender.com",
)
_clean_hosts = []
for _h in _raw_allowed:
    _h = _h.strip()
    if _h.startswith("https://"):
        _h = _h[len("https://") :]
    elif _h.startswith("http://"):
        _h = _h[len("http://") :]
    _h = _h.split("/")[0].split(":")[0]
    if _h:
        _clean_hosts.append(_h)

ALLOWED_HOSTS = sorted(
    list(set(_clean_hosts + ["localhost", "127.0.0.1", ".onrender.com", "testserver"]))
)

_raw_csrf = env_list(
    "CSRF_TRUSTED_ORIGINS",
    "https://*.onrender.com",
)
_clean_csrf = []
for _origin in _raw_csrf:
    _origin = _origin.strip().rstrip("/")
    if _origin and not (_origin.startswith("http://") or _origin.startswith("https://")):
        _origin = f"https://{_origin}"
    if _origin:
        _clean_csrf.append(_origin)

CSRF_TRUSTED_ORIGINS = sorted(
    list(set(_clean_csrf + ["https://*.onrender.com"]))
)

# Hardening — safe defaults for a deployed service sitting behind HTTPS
IS_DEPLOYED = os.getenv("IS_DEPLOYED", "False") == "True"

# BACKEND_URL defaults to localhost so local development needs no config -
# which would be silent poison here, mailing out invitation and email-change
# links nobody can open. A deployed instance has to say where it lives.
if IS_DEPLOYED and "127.0.0.1" in BACKEND_URL:  # noqa: F405
    raise ImproperlyConfigured(
        "BACKEND_URL still points at localhost. Set it to this deployment's "
        "public URL - every link this API emails is built from it."
    )

if IS_DEPLOYED:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
else:
    # If we are in "production" mode but running locally (for testing),
    # disable SSL redirection so you can actually access the site.
    SECURE_SSL_REDIRECT = False

# The pepper itself is enforced in base.py now, for every environment.

# The per-school / per-institute SMTP app passwords are encrypted under this
# key. Locally it is derived from DJANGO_SECRET_KEY for convenience; deployed,
# that ties the two secrets together and rotating one silently breaks the
# other, so it has to be its own value.
if IS_DEPLOYED and not EMAIL_SECRETS_KEYS:  # noqa: F405
    import warnings
    warnings.warn(
        "EMAIL_SECRETS_KEY is not set; falling back to key derived from SECRET_KEY. "
        "For maximum production isolation, set EMAIL_SECRETS_KEY in your environment.",
        UserWarning,
    )
