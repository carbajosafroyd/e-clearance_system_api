"""Thread helper for the fire-and-forget generation jobs."""

import logging
import threading
import traceback
from typing import Any, Callable, Optional

from django.db import connections

logger = logging.getLogger(__name__)


def run_in_background(
    target: Callable[..., Any], *, name: Optional[str] = None, **kwargs: Any
) -> threading.Thread:
    """
    Run `target(**kwargs)` on a daemon thread, and always hand the database
    connection back when it finishes.

    Django only closes connections around the request/response cycle. A thread
    we spawn ourselves opens its own connection and never releases it, so over
    enough generation runs the process walks straight into MySQL's
    max_connections. `connections.close_all()` in the finally block is what
    stops that.

    Known limit: a daemon thread dies with its worker process, so a gunicorn
    restart mid-run loses the work silently. Moving generation onto a real
    queue (Celery/RQ) is the actual fix; this wrapper only makes the current
    approach safe to keep running in the meantime.
    """

    def _run() -> None:
        try:
            target(**kwargs)
        except Exception:
            logger.error(
                "background task %s crashed\n%s",
                getattr(target, "__name__", target),
                traceback.format_exc(),
            )
        finally:
            connections.close_all()

    thread = threading.Thread(
        target=_run,
        name=name or getattr(target, "__name__", "fees-background"),
        daemon=True,
    )
    thread.start()
    return thread
