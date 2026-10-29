"""
Caching for the fees app.

Everything in here is built on one idea: **version-stamped keys**.

Instead of remembering every key we have ever written so we can delete them
again later, each cached value is stored under a key that carries the current
version number of its namespace:

    fees:list:v7:fee-list:u12:a1b2c3d4e5f60718

Invalidating is then just bumping that number:

    fees:list:v7:...   <- what everybody was reading a second ago
    fees:list:v8:...   <- what everybody reads from now on

Nothing is deleted. The v7 entries simply become unreachable and fall out of
the cache on their own TTL. That matters because this project runs on Django's
default LocMemCache, which has no "delete every key matching this prefix" -
only django-redis has `delete_pattern`. Bumping one counter is the portable
way to drop a whole family of entries at once.

Namespaces, and what makes each one stale
-----------------------------------------
    LIST_NS        /fees/ list responses
                   bumped by: any Fee write, any GeneratedFeeBatch write

    BATCH_NS       /generated-fee-batch/ list responses
                   bumped by: any GeneratedFeeBatch write

    ATTENDANCE_NS  /fees/{id}/attendance-details/
                   /fees/{id}/attendance-settings/
                   bumped by: any Fee write, any AttendanceRecord write,
                   any InstituteAttendanceEventSetting write

The bumping itself lives in `fees/signals.py` for ordinary saves and deletes,
and is called by hand from the two places that write rows without firing model
signals (`services/generation/engine.py`, `services/overdue_service.py`).

Caching a new endpoint
----------------------
    from .. import cache as fee_cache

    @action(detail=True, methods=["get"])
    def my_endpoint(self, request, pk=None):
        fee = self.get_object()          # keep this: it runs the permissions
        data = fee_cache.get_or_set(
            fee_cache.ATTENDANCE_NS,
            ["my-endpoint", fee.pk],
            lambda: expensive_lookup(fee),
        )
        return Response(data)

Pick the namespace whose invalidation rules already match your data. If none
of them do, add a namespace here and a receiver in `signals.py` for whichever
model going stale would be a bug.

Which backend this runs on
--------------------------
Whatever `CACHES` in `config/settings/base.py` points at - none of the code
here cares. Set `REDIS_URL` and it is Redis; leave it unset and it is Django's
built-in in-memory cache. The only difference that reaches this module is the
TTL, which that same settings block picks for you.

It does matter for how exact invalidation is, though:

* **On Redis** every worker shares one cache, so a bump reaches all of them at
  once. An edit is reflected on the very next request, always. This is the
  mode the design assumes.
* **On the in-memory fallback** each worker process has its own cache. Worker
  A bumping its own version does not touch worker B's, so B can keep serving a
  stale page until the entry times out - which is why the fallback TTL is a
  short 60 seconds rather than the 30 minutes used with Redis. On a
  single-worker deployment this distinction does not exist and invalidation is
  exact either way.

Known limits
------------
* **A build() that returns None is never cached**, because `None` is also what
  the cache returns on a miss. Empty lists and empty dicts cache fine.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Callable, Sequence

from django.conf import settings
from django.core.cache import cache
from django.db import transaction

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Namespaces
# --------------------------------------------------------------------------

LIST_NS = "fees:list"
BATCH_NS = "fees:batch"
ATTENDANCE_NS = "fees:attendance"

ALL_NAMESPACES = (LIST_NS, BATCH_NS, ATTENDANCE_NS)


# How long a cached value may live. Set in `config/settings/base.py`, where it
# follows the backend: generous on Redis, where invalidation has already
# removed anything stale, and short on the in-memory fallback, where the TTL
# is the only thing bounding how long another worker can serve an old page.
DEFAULT_TIMEOUT = settings.CACHE_DEFAULT_TTL

# Version counters, on the other hand, must never expire on their own. If a
# counter disappeared while entries stamped with it were still cached, the
# version would restart at 1 and those stale entries would become readable
# again.
_VERSION_TIMEOUT = None


# --------------------------------------------------------------------------
# Versions
# --------------------------------------------------------------------------


def _version_key(namespace: str) -> str:
    return f"{namespace}:version"


def current_version(namespace: str) -> int:
    """The version every key in this namespace is currently stamped with."""
    # `or 1` covers the one gap in get_or_set: on a backend that just failed
    # to write, it hands back None rather than the default.
    return cache.get_or_set(_version_key(namespace), 1, _VERSION_TIMEOUT) or 1


def invalidate(*namespaces: str) -> None:
    """
    Drop everything cached under these namespaces, by bumping their versions.

    Safe to call as often as you like - it is one cache increment per
    namespace, no matter how many entries are being orphaned.
    """
    for namespace in namespaces:
        key = _version_key(namespace)
        try:
            new_version = cache.incr(key)
        except ValueError:
            # No counter yet: either this is the first write since the process
            # started, or the counter was culled. Either way nothing readable
            # is stamped with a version, so starting over is safe.
            cache.set(key, 1, _VERSION_TIMEOUT)
            new_version = 1
        logger.debug(
            "fees.cache invalidated namespace=%s new_version=%s", namespace, new_version
        )


def invalidate_after_commit(*namespaces: str) -> None:
    """
    Same as `invalidate()`, but held until the surrounding transaction commits.

    Bumping mid-transaction is a real bug, not a nicety: another request could
    repopulate the cache from rows we have written but not yet committed, and
    if the transaction then rolls back the cache keeps serving data that never
    existed. Outside a transaction Django runs the callback immediately, so
    this is always the right call to make.
    """
    transaction.on_commit(lambda: invalidate(*namespaces))


def invalidate_all() -> None:
    """Drop every fees cache. Handy from the shell after a manual DB fix."""
    invalidate(*ALL_NAMESPACES)


# --------------------------------------------------------------------------
# Keys
# --------------------------------------------------------------------------


def digest(value: str) -> str:
    """A short, stable hash - keeps keys short and free of odd characters."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def build_key(namespace: str, parts: Sequence[Any]) -> str:
    """`<namespace>:v<version>:<parts joined by colons>`."""
    suffix = ":".join(str(part) for part in parts)
    return f"{namespace}:v{current_version(namespace)}:{suffix}"


def request_parts(request, label: str) -> list[str]:
    """
    Key parts for a list endpoint, covering everything that changes its output.

    * `label` - which endpoint, so two views sharing a namespace cannot collide.
    * the user id - `get_queryset()` narrows by who is asking. A superuser,
      institute staff and a student all get different rows from the same URL
      and must never share a cache entry.
    * the query string - every filter, search term, ordering and page number,
      hashed whole so nothing has to be listed here by hand. Sorted first, so
      `?status=paid&per_page=10` and `?per_page=10&status=paid` share an entry.
    """
    query = "&".join(
        f"{key}={value}"
        for key, values in sorted(request.query_params.lists())
        for value in sorted(values)
    )
    return [label, f"u{request.user.pk}", digest(query)]


# --------------------------------------------------------------------------
# Reading and writing
# --------------------------------------------------------------------------


def read(key: str) -> Any:
    """Cache lookup with a log line. Returns None on a miss."""
    value = cache.get(key)
    logger.debug("fees.cache %s key=%s", "hit" if value is not None else "miss", key)
    return value


def write(key: str, value: Any, timeout: int = DEFAULT_TIMEOUT) -> Any:
    """Store a value and hand it straight back, so callers can `return write(...)`."""
    cache.set(key, value, timeout)
    return value


def get_or_set(
    namespace: str,
    parts: Sequence[Any],
    build: Callable[[], Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> Any:
    """
    Return the cached value for these key parts, or build it and cache it.

    `build` is only called on a miss. A `build` that returns None is not
    cached - see "Known limits" in the module docstring.
    """
    key = build_key(namespace, parts)

    value = read(key)
    if value is not None:
        return value

    value = build()
    if value is None:
        return None
    return write(key, value, timeout)
