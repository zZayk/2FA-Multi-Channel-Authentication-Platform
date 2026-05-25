"""
Placeholder for the DLR (Delivery Receipt) polling task.

The real implementation lands in Week 5 (Month 2). For now this stub:
  - Registers a Celery task so the worker has something to load and log.
  - Documents the contract Beat will eventually schedule against.

[LEARN]
Beat schedule entries point at a fully-qualified task name. To swap the
implementation later, we keep this module's import path stable
(`app.tasks.dlr.poll_pending`) — Beat config keeps working unchanged.
"""

from __future__ import annotations

import logging

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.dlr.poll_pending")
def poll_pending() -> dict[str, int]:
    """
    Stub. Real version (Week 5) will:
      1. Load OTP rows where status IN ('sent') AND created_at > now-N minutes.
      2. Hit TunisiaSMS DLR endpoint in batches.
      3. Update status to DELIVERED / FAILED / leave SENT.
      4. On FAILED → enqueue Email fallback (Celery chord).

    Returns a small summary dict for monitoring.
    """
    logger.info("dlr.poll_pending.stub", extra={"event": "dlr_stub"})
    return {"polled": 0, "delivered": 0, "failed": 0}