"""
health.views
============

Three endpoints, matching the convention used by Kubernetes and most
cloud load balancers:

  GET /health/live/    -> liveness probe   (is the process up at all?)
  GET /health/ready/   -> readiness probe  (can it serve traffic right now?)
  GET /health/         -> full detailed report (dashboards, on-call, status pages)

No authentication is required by design — orchestrators (Kubernetes,
ECS, load balancers) call these anonymously. Do not put secrets or
internal topology details in the response.
"""
import time

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from django.utils.decorators import method_decorator

from .checks import (
    STATUS_HEALTHY,
    STATUS_DEGRADED,
    STATUS_UNHEALTHY,
    get_active_checks,
)

_PROCESS_START = time.time()


def _overall_status(statuses: list[str]) -> str:
    if all(s == STATUS_HEALTHY for s in statuses):
        return STATUS_HEALTHY
    if any(s == STATUS_UNHEALTHY for s in statuses):
        return STATUS_UNHEALTHY
    return STATUS_DEGRADED


def _http_status_for(overall: str) -> int:
    # Readiness/full checks: unhealthy or degraded should pull the pod
    # out of the load balancer rotation, hence non-200.
    return 200 if overall == STATUS_HEALTHY else 503


def _run_checks(check_names=None) -> dict:
    active = get_active_checks()
    if check_names:
        active = {k: v for k, v in active.items() if k in check_names}

    results = {name: fn() for name, fn in active.items()}
    return results


@method_decorator(never_cache, name="dispatch")
@method_decorator(require_GET, name="dispatch")
class LivenessView(View):
    """Cheap, dependency-free check that the process is alive and serving requests.

    Never checks the database, cache, or any external dependency —
    that's the readiness probe's job. Keep this fast (<10ms) since
    orchestrators call it frequently.
    """

    def get(self, request):
        return JsonResponse(
            {
                "status": STATUS_HEALTHY,
                "timestamp": timezone.now().isoformat(),
                "uptime_seconds": round(time.time() - _PROCESS_START, 2),
            },
            status=200,
        )


@method_decorator(never_cache, name="dispatch")
@method_decorator(require_GET, name="dispatch")
class ReadinessView(View):
    """Checks the dependencies actually required to serve real traffic.

    Configure which checks count toward readiness via:
        HEALTH_CHECK_READINESS = ["database", "cache"]
    Defaults to database + cache if unset.
    """

    def get(self, request):
        readiness_checks = getattr(
            settings, "HEALTH_CHECK_READINESS", ["database", "cache"]
        )
        results = _run_checks(readiness_checks)
        statuses = [r.status for r in results.values()]
        overall = _overall_status(statuses) if statuses else STATUS_HEALTHY

        payload = {
            "status": overall,
            "timestamp": timezone.now().isoformat(),
            "checks": {name: r.to_dict() for name, r in results.items()},
        }
        return JsonResponse(payload, status=_http_status_for(overall))


@method_decorator(never_cache, name="dispatch")
@method_decorator(require_GET, name="dispatch")
class HealthDetailView(View):
    """Full diagnostic report across every registered check.

    Intended for internal dashboards, status pages, and on-call
    tooling rather than high-frequency automated polling.
    """

    def get(self, request):
        results = _run_checks()
        statuses = [r.status for r in results.values()]
        overall = _overall_status(statuses) if statuses else STATUS_HEALTHY

        payload = {
            "status": overall,
            "timestamp": timezone.now().isoformat(),
            "uptime_seconds": round(time.time() - _PROCESS_START, 2),
            "version": getattr(settings, "HEALTH_CHECK_VERSION", None)
            or getattr(settings, "APP_VERSION", "unknown"),
            "environment": getattr(settings, "HEALTH_CHECK_ENVIRONMENT", None)
            or ("debug" if settings.DEBUG else "production"),
            "checks": {name: r.to_dict() for name, r in results.items()},
        }
        return JsonResponse(payload, status=_http_status_for(overall))