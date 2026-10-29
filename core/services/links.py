"""
Absolute URLs for the links we put in emails.

One setting, `BACKEND_URL`, decides where every emailed link points. Set it
to `http://127.0.0.1:8000` and a locally-triggered invitation lands on your
local server; set it to the deployed host and it lands there. That is the
whole point of it being one value rather than a local/deployed pair — there
is never a question of which of the two a given email used.

Why not build it from the request? Because an email outlives the request
that sent it, most of ours are rendered on a background thread with no
request in sight, and behind a proxy `build_absolute_uri` happily names an
internal host the recipient cannot reach.
"""

from django.conf import settings


def backend_url() -> str:
    """The public base URL of this API, without a trailing slash."""
    return (getattr(settings, "BACKEND_URL", "") or "").rstrip("/")


def backend_link(path: str, **query) -> str:
    """
    An absolute link to `path` on this API.

        backend_link("/api/v1/register/", token=token)
        -> "http://127.0.0.1:8000/api/v1/register/?token=abc123"
    """
    from urllib.parse import urlencode

    url = f"{backend_url()}/{path.lstrip('/')}"
    return f"{url}?{urlencode(query)}" if query else url


def frontend_url() -> str:
    """Where the SPA lives — for "back to the app" buttons on landing pages."""
    return (getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
