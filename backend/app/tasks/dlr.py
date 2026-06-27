"""
DLR (Delivery Receipt) polling task — the Month 2 reconciliation loop.

[LEARN]
Pattern: "Reconciliation loop" (a.k.a. poll-based eventual consistency).
SMS delivery is asynchronous: the send call returns 202/ACCEPTED in
milliseconds, but the carrier confirms (or fails) the handset delivery
seconds-to-minutes later via a *Delivery Receipt*. We don't get a push, so
Celery Beat fires this task on a timer and we *poll* the provider to advance
each in-flight OTP from SENT → DELIVERED / FAILED.

Why poll instead of a webhook?
  TunisiaSMS can push DLRs to a webhook, but that needs a public HTTPS
  endpoint + signature verification + retry handling. Polling is the thin
  first slice: no inbound surface, works behind NAT, easy to test. A webhook
  receiver is a Month-4 hardening upgrade — and the two can coexist (webhook
  fast-path, poll as the safety net).

State transitions this task owns:
  SENT --DELIVERED--> DELIVERED        (carrier confirmed)
  SENT --FAILED------> FAILED          (carrier rejected/undeliverable)
  SENT --(age > timeout)--> FAILED     (timeout sweep — never wedge forever)

The timeout sweep is the load-bearing safety net: a provider that simply
stops answering for a message must not leave that OTP stuck in SENT. Past
`DLR_TIMEOUT_SECONDS` we declare it FAILED without a (pointless) DLR call.

Fallback policy (Slice 2): fallback is **user-initiated**, not automatic.
When an SMS reaches FAILED the client sees it via GET /otp/{id} and may offer
the user "try another method" → POST /otp/resend on the email channel. This
task therefore only records the terminal FAILED state (and logs that a
fallback is now available); it never enqueues a second channel itself.

Read more:
  - https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html
  - "Reconciliation loops" — Kubernetes controller pattern, same idea
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import TaskSessionLocal
from app.models.otp import OTP, OTPChannel, OTPStatus
from app.services.channels.base import DlrStatus, get_adapter

# Side-effect import — registers the SMS adapter in the worker process so
# get_adapter(OTPChannel.SMS) resolves. (Same reason dispatch.py imports it.)
import app.services.channels.sms  # noqa: F401

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _signal_fallback_available(otp: OTP) -> None:
    """
    Record that an SMS terminally failed, so a fallback is now offerable.

    [LEARN] Fallback is user-initiated (product decision): we do NOT auto-send
    a second channel. We just log it — the client polls GET /otp/{id}, sees
    FAILED, and may call POST /otp/resend on the user's behalf. Emitting a
    distinct log event keeps this observable (alerting / dashboard) without
    coupling the poll loop to the email path.
    """
    logger.info(
        "dlr.fallback_available",
        extra={
            "otp_id": str(otp.id),
            "recipient": otp.recipient,
            "correlation_id": str(otp.correlation_id),
        },
    )


# =============================================================================
# Async core
# =============================================================================

async def _poll_pending_async() -> dict[str, int]:
    settings = get_settings()
    now = _utcnow()
    timeout_cutoff = now - timedelta(seconds=settings.DLR_TIMEOUT_SECONDS)

    summary = {
        "polled": 0,
        "delivered": 0,
        "failed": 0,
        "timed_out": 0,
        "pending": 0,
    }

    async with TaskSessionLocal() as session:
        # In-flight SMS rows only. Email never reaches SENT-awaiting-DLR (no
        # async receipt). Oldest first so the batch limit clears the most
        # at-risk rows (closest to / past timeout) first.
        stmt = (
            select(OTP)
            .where(
                OTP.status == OTPStatus.SENT,
                OTP.channel == OTPChannel.SMS,
                OTP.provider_message_id.is_not(None),
            )
            .order_by(OTP.sent_at.asc())
            .limit(settings.DLR_POLL_BATCH_SIZE)
        )
        rows = (await session.execute(stmt)).scalars().all()

        if not rows:
            return summary

        adapter = get_adapter(OTPChannel.SMS)

        for otp in rows:
            summary["polled"] += 1

            # Timeout sweep first — skip the DLR call for ancient rows.
            if otp.sent_at is not None and otp.sent_at < timeout_cutoff:
                otp.status = OTPStatus.FAILED
                otp.failed_at = now
                summary["timed_out"] += 1
                logger.warning(
                    "dlr.timed_out",
                    extra={
                        "otp_id": str(otp.id),
                        "sent_at": otp.sent_at.isoformat(),
                        "correlation_id": str(otp.correlation_id),
                    },
                )
                _signal_fallback_available(otp)
                continue

            result = await adapter.query_dlr(
                provider_message_id=otp.provider_message_id,
                correlation_id=otp.correlation_id,
            )

            if result.status is DlrStatus.DELIVERED:
                otp.status = OTPStatus.DELIVERED
                otp.delivered_at = now
                summary["delivered"] += 1
                logger.info(
                    "dlr.delivered",
                    extra={
                        "otp_id": str(otp.id),
                        "correlation_id": str(otp.correlation_id),
                    },
                )
            elif result.status is DlrStatus.FAILED:
                otp.status = OTPStatus.FAILED
                otp.failed_at = now
                summary["failed"] += 1
                logger.warning(
                    "dlr.failed",
                    extra={
                        "otp_id": str(otp.id),
                        "reason": result.error_reason,
                        "correlation_id": str(otp.correlation_id),
                    },
                )
                _signal_fallback_available(otp)
            else:
                # PENDING / UNKNOWN — still in flight. Leave SENT; a later tick
                # (or the timeout sweep) decides. Never mark DELIVERED on doubt.
                summary["pending"] += 1

        await session.commit()

    return summary


# =============================================================================
# Celery task
# =============================================================================

@celery_app.task(name="app.tasks.dlr.poll_pending")
def poll_pending() -> dict[str, int]:
    """
    Reconcile in-flight SMS OTPs against TunisiaSMS delivery receipts.

    Scheduled by Beat every `DLR_POLL_INTERVAL_SECONDS`. Returns a summary
    dict for monitoring (counts per transition this tick).
    """
    summary = asyncio.run(_poll_pending_async())
    logger.info("dlr.poll_pending.done", extra={"event": "dlr_poll", **summary})
    return summary