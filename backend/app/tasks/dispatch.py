"""
Dispatch task — picks the right channel adapter, renders the template, sends.

[LEARN]
Pattern: "Async work inside a sync Celery task" via `asyncio.run`.
Celery's task model is synchronous. To run async I/O (httpx, aiosmtplib,
async SQLAlchemy) we bridge with `asyncio.run(coro)`. A fresh event loop
spins per task call — matches the per-call adapter client decision and
isolates side effects.

Retry policy:
  `autoretry_for=(TransientChannelError,)` re-enqueues on retryable
  failures only. `retry_backoff=5` + `retry_jitter=True` spaces retries
  with randomised exponential backoff — avoids thundering-herd on a
  flapping upstream.

Why we import channels.* at module top:
  Side-effect imports register concrete adapters with the registry. If
  the worker boots without these imports, `get_adapter()` raises
  LookupError. Module-level imports run once when Celery loads tasks.

Read more:
  - https://docs.celeryq.dev/en/stable/userguide/tasks.html#automatic-retry-for-known-exceptions
  - https://docs.python.org/3/library/asyncio-runner.html
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.otp import OTP, OTPStatus
from app.services.channels import (  # noqa: F401 — public re-exports
    PermanentChannelError,
    SendOutcome,
    TransientChannelError,
)
from app.services.channels.base import get_adapter

# Side-effect imports — register adapters in the worker process.
import app.services.channels.sms    # noqa: F401
import app.services.channels.email  # noqa: F401

from app.services.templates import render_otp
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


# =============================================================================
# Async core
# =============================================================================

async def _dispatch_async(otp_id: uuid.UUID, code: str) -> str:
    """
    Returns the final OTPStatus value (string) for observability.
    Raises TransientChannelError → Celery retries.
    """
    async with SessionLocal() as session:
        otp = await session.get(OTP, otp_id)
        if otp is None:
            # Row vanished — possible test cleanup or race. Permanent.
            logger.error("dispatch.otp_not_found", extra={"otp_id": str(otp_id)})
            return "not_found"

        # Idempotency: skip if already past PENDING (manual replay safety).
        if otp.status not in (OTPStatus.PENDING,):
            logger.info(
                "dispatch.skip_non_pending",
                extra={"otp_id": str(otp.id), "status": otp.status.value},
            )
            return otp.status.value

        adapter = get_adapter(otp.channel)
        rendered = render_otp(otp.channel, code=code)

        try:
            result = await adapter.send(
                recipient=otp.recipient,
                body=rendered.body,
                subject=rendered.subject,
                correlation_id=otp.correlation_id,
            )
        except PermanentChannelError as e:
            logger.error(
                "dispatch.permanent_failure",
                extra={
                    "otp_id": str(otp.id),
                    "channel": otp.channel.value,
                    "error": str(e),
                },
            )
            otp.status = OTPStatus.FAILED
            await session.commit()
            return OTPStatus.FAILED.value

        # Map send outcome → OTP status transition.
        if result.outcome is SendOutcome.ACCEPTED:
            otp.status = OTPStatus.SENT
            logger.info(
                "dispatch.sent",
                extra={
                    "otp_id": str(otp.id),
                    "channel": otp.channel.value,
                    "provider_message_id": result.provider_message_id,
                    "correlation_id": str(otp.correlation_id),
                },
            )
        elif result.outcome is SendOutcome.RETRY:
            # Re-raise so Celery autoretry picks it up.
            await session.rollback()
            raise TransientChannelError(
                f"adapter returned RETRY: {result.error_reason}"
            )
        else:  # REJECTED
            otp.status = OTPStatus.FAILED
            logger.warning(
                "dispatch.rejected",
                extra={
                    "otp_id": str(otp.id),
                    "channel": otp.channel.value,
                    "reason": result.error_reason,
                },
            )

        await session.commit()
        return otp.status.value


# =============================================================================
# Celery task
# =============================================================================

@celery_app.task(
    name="app.tasks.dispatch.dispatch_otp",
    bind=True,
    autoretry_for=(TransientChannelError,),
    max_retries=3,
    retry_backoff=5,        # 5s, 10s, 20s
    retry_backoff_max=60,
    retry_jitter=True,
    acks_late=True,
)
def dispatch_otp(self, otp_id: str, code: str) -> str:
    """
    Send the OTP on its configured channel.

    Args:
      otp_id: stringified UUID — Celery serialises args as JSON, so we
              accept str and parse here.
      code:   plaintext OTP. See Security note in docs/decisions.md —
              v1 accepts plaintext-in-broker for simplicity.
    """
    logger.info(
        "dispatch.start",
        extra={"otp_id": otp_id, "retry": self.request.retries},
    )
    return asyncio.run(_dispatch_async(uuid.UUID(otp_id), code))