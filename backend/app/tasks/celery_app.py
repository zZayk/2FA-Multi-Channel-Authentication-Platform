"""
Celery application instance — single source of truth for the task queue.

[LEARN]
Pattern: "Celery application factory".
We build one `Celery(...)` object, configure it, then let it autodiscover
task modules under `app.tasks`. The worker and beat containers both run
this same module — they're the same image, different commands:

    worker:  celery -A app.tasks.celery_app worker --loglevel=INFO
    beat:    celery -A app.tasks.celery_app beat   --loglevel=INFO

Read more:
  - https://docs.celeryq.dev/en/stable/userguide/application.html
  - https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab  # noqa: F401 — used by Beat schedules later

from app.core.config import get_settings

settings = get_settings()

# [LEARN] First arg is the *main module name*, used to auto-name tasks
# (e.g. "app.tasks.dlr.poll_status") and to namespace registry entries.
celery_app = Celery(
    "app",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
# [LEARN] JSON serializer (not pickle) — pickle deserializes arbitrary
# Python objects from the broker. If the broker is ever compromised,
# pickle = RCE. JSON is data-only.
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Idempotency / reliability:
    task_acks_late=True,           # ack only after task returns — survive worker crash mid-task
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # fairer distribution; relevant once tasks are slow (channel I/O)
    # Visibility timeout for Redis broker — > longest task duration.
    broker_transport_options={"visibility_timeout": 3600},
)

# -----------------------------------------------------------------------------
# Periodic schedule (Beat)
# -----------------------------------------------------------------------------
# Real DLR polling task lands in Week 5 (Month 2). Empty schedule for now —
# Beat boots cleanly with nothing to fire, then we'll add entries like:
#   "poll-tunisiasms-dlr": {
#       "task": "app.tasks.dlr.poll_pending",
#       "schedule": 30.0,   # every 30 seconds
#   },
celery_app.conf.beat_schedule = {}

# -----------------------------------------------------------------------------
# Task autodiscovery
# -----------------------------------------------------------------------------
# Picks up every module matching `app.tasks.*` that defines @celery_app.task.
# Add new task modules to app/tasks/ — no manual registration needed.
celery_app.autodiscover_tasks(packages=["app.tasks"])