"""
health.checks
=============

Each check is a small callable that returns a HealthCheckResult.
Checks never raise — any exception is caught and turned into an
"unhealthy" result, because a broken check must never break the
health endpoint itself.

Add your own checks by decorating a function with @register_check
or by appending to HEALTH_CHECKS in settings (see README.md).
"""
from __future__ import annotations

import shutil
import time
import socket
from dataclasses import dataclass, field
from typing import Callable, Optional

from django.conf import settings
from django.db import connections
from django.db.utils import OperationalError

try:
    from django.core.cache import caches
except ImportError:  # pragma: no cover
    caches = None


STATUS_HEALTHY = "healthy"
STATUS_DEGRADED = "degraded"
STATUS_UNHEALTHY = "unhealthy"


@dataclass
class HealthCheckResult:
    name: str
    status: str
    response_time_ms: float
    details: dict = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        payload = {
            "status": self.status,
            "response_time_ms": self.response_time_ms,
        }
        if self.details:
            payload["details"] = self.details
        if self.error:
            payload["error"] = self.error
        return payload


def timed_check(name: str):
    """Decorator that times a check and guarantees it never raises."""

    def decorator(func: Callable[[], dict]):
        def wrapper() -> HealthCheckResult:
            start = time.perf_counter()
            try:
                details = func() or {}
                elapsed = round((time.perf_counter() - start) * 1000, 2)
                return HealthCheckResult(
                    name=name,
                    status=STATUS_HEALTHY,
                    response_time_ms=elapsed,
                    details=details,
                )
            except Exception as exc:  # noqa: BLE001 - intentional catch-all
                elapsed = round((time.perf_counter() - start) * 1000, 2)
                return HealthCheckResult(
                    name=name,
                    status=STATUS_UNHEALTHY,
                    response_time_ms=elapsed,
                    error=str(exc),
                )

        wrapper.check_name = name  # type: ignore[attr-defined]
        return wrapper

    return decorator


@timed_check("database")
def check_database() -> dict:
    """Runs a trivial query against every configured database alias."""
    details = {}
    for alias in connections:
        conn = connections[alias]
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        details[alias] = "connected"
    return details


@timed_check("cache")
def check_cache() -> dict:
    """Writes then reads a throwaway key from every configured cache."""
    if caches is None:
        return {"skipped": "cache framework unavailable"}

    details = {}
    cache_names = getattr(settings, "CACHES", {}).keys() or ["default"]
    probe_key = "__health_check_probe__"
    for name in cache_names:
        cache = caches[name]
        cache.set(probe_key, "ok", timeout=5)
        if cache.get(probe_key) != "ok":
            raise RuntimeError(f"cache '{name}' failed round-trip test")
        details[name] = "connected"
    return details


@timed_check("disk")
def check_disk() -> dict:
    """Fails if free disk space drops below HEALTH_CHECK_DISK_MIN_PERCENT (default 10%)."""
    min_percent = getattr(settings, "HEALTH_CHECK_DISK_MIN_PERCENT", 10)
    total, used, free = shutil.disk_usage("/")
    free_percent = round((free / total) * 100, 2)
    details = {
        "free_percent": free_percent,
        "free_gb": round(free / (1024 ** 3), 2),
        "total_gb": round(total / (1024 ** 3), 2),
    }
    if free_percent < min_percent:
        raise RuntimeError(
            f"free disk space {free_percent}% below threshold {min_percent}%"
        )
    return details


@timed_check("dns")
def check_dns() -> dict:
    """Sanity check that outbound DNS resolution works (catches container network issues)."""
    host = getattr(settings, "HEALTH_CHECK_DNS_HOST", "www.google.com")
    socket.gethostbyname(host)
    return {"resolved": host}



BUILTIN_CHECKS = {
    "database": check_database,
    "cache": check_cache,
    "disk": check_disk,
    "dns": check_dns,
}

_custom_registry: dict[str, Callable[[], HealthCheckResult]] = {}


def register_check(name: str):
    """Decorator for registering an additional check at import time.

    Example
    -------
    from health.checks import register_check, timed_check

    @register_check("celery")
    @timed_check("celery")
    def check_celery():
        ...
        return {"workers": 3}
    """

    def decorator(func):
        _custom_registry[name] = func
        return func

    return decorator


def get_active_checks() -> dict[str, Callable[[], HealthCheckResult]]:
    """Resolves the final set of checks to run, respecting settings.HEALTH_CHECKS."""
    configured = getattr(settings, "HEALTH_CHECKS", None)
    all_checks = {**BUILTIN_CHECKS, **_custom_registry}

    if configured is None:
        return all_checks
    return {name: all_checks[name] for name in configured if name in all_checks}