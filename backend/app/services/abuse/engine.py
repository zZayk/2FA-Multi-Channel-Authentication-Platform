"""
Anti-abuse engine — the gate the OTP send path calls before issuing a code.

[LEARN]
Pattern: "Layered rule engine with short-circuit + explainable decision".
We evaluate cheapest-and-most-certain first, returning the moment one layer
says "block", and we always carry a human-readable `reason` so a block is
explainable (to logs, to an admin, to a support ticket) — never a silent 403.

  1. Blacklist  — hard denylist (indexed equality). Known-bad → block now.
  2. Rate rules — per-recipient hour/day caps + per-IP hour cap. Volume abuse.
  3. ML         — Isolation Forest anomaly score (scaffold; see ml.py).

Each layer is independently testable; the send path only sees `AbuseDecision`.

Read more:
  - OWASP "Blocking Brute Force Attacks"
  - "Fail closed vs fail open" — we fail OPEN here (allow) on engine error,
    logged loudly, because a 2FA outage (locking everyone out) is worse than
    one slipped request. Revisit per threat model.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.blacklist import BlacklistEntry, BlacklistKind
from app.models.otp import OTP, OTPStatus
from app.services.abuse.ml import anomaly_score

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class AbuseDecision:
    """Result of evaluating a send request against the abuse engine."""

    allowed: bool
    reason: str | None = None   # human-readable, e.g. "rate_limit_hour: 11>=10"
    rule: str | None = None     # which layer fired: blacklist|rate|ml
    score: float = 0.0          # ML anomaly score (0.0 while stubbed)


# =============================================================================
# Blacklist helpers
# =============================================================================

async def _is_blacklisted(session: AsyncSession, value: str) -> bool:
    stmt = select(BlacklistEntry.id).where(BlacklistEntry.value == value).limit(1)
    return (await session.execute(stmt)).first() is not None


async def add_to_blacklist(
    session: AsyncSession,
    *,
    value: str,
    kind: BlacklistKind,
    reason: str | None = None,
) -> BlacklistEntry:
    """Add (idempotently) a recipient/IP to the denylist."""
    existing = (
        await session.execute(
            select(BlacklistEntry).where(BlacklistEntry.value == value)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    entry = BlacklistEntry(id=uuid.uuid4(), value=value, kind=kind, reason=reason)
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


async def remove_from_blacklist(session: AsyncSession, *, value: str) -> bool:
    """Remove a value from the denylist. Returns True if something was removed."""
    entry = (
        await session.execute(
            select(BlacklistEntry).where(BlacklistEntry.value == value)
        )
    ).scalar_one_or_none()
    if entry is None:
        return False
    await session.delete(entry)
    await session.commit()
    return True


async def list_blacklist(session: AsyncSession) -> list[BlacklistEntry]:
    stmt = select(BlacklistEntry).order_by(BlacklistEntry.created_at.desc())
    return list((await session.execute(stmt)).scalars().all())


# =============================================================================
# Rate rules
# =============================================================================

async def _count_recent(
    session: AsyncSession, *, column, value: str, since: datetime
) -> int:
    """Count non-blocked OTPs for `column == value` created since `since`."""
    stmt = (
        select(func.count())
        .select_from(OTP)
        .where(
            column == value,
            OTP.created_at >= since,
            OTP.status != OTPStatus.BLOCKED,  # don't let blocked attempts compound
        )
    )
    return (await session.execute(stmt)).scalar_one()


# =============================================================================
# Engine
# =============================================================================

async def evaluate(
    session: AsyncSession,
    *,
    recipient: str,
    ip: str | None = None,
) -> AbuseDecision:
    """
    Decide whether to allow issuing an OTP to `recipient` (from `ip`).

    Returns an `AbuseDecision`; the caller turns a disallow into HTTP 429 +
    records a BLOCKED audit row. Fails OPEN on unexpected error (logged).
    """
    settings = get_settings()
    now = _utcnow()

    try:
        # --- 1. Blacklist short-circuit ---
        if await _is_blacklisted(session, recipient):
            return AbuseDecision(False, "blacklisted_recipient", "blacklist", 1.0)
        if ip and await _is_blacklisted(session, ip):
            return AbuseDecision(False, "blacklisted_ip", "blacklist", 1.0)

        # --- 2. Rate rules ---
        hour_ago = now - timedelta(hours=1)
        day_ago = now - timedelta(days=1)

        per_hour = await _count_recent(
            session, column=OTP.recipient, value=recipient, since=hour_ago
        )
        if per_hour >= settings.ABUSE_RULE_MAX_PER_HOUR:
            return AbuseDecision(
                False,
                f"rate_limit_hour: {per_hour}>={settings.ABUSE_RULE_MAX_PER_HOUR}",
                "rate",
            )

        per_day = await _count_recent(
            session, column=OTP.recipient, value=recipient, since=day_ago
        )
        if per_day >= settings.ABUSE_RULE_MAX_PER_DAY:
            return AbuseDecision(
                False,
                f"rate_limit_day: {per_day}>={settings.ABUSE_RULE_MAX_PER_DAY}",
                "rate",
            )

        if ip:
            per_hour_ip = await _count_recent(
                session, column=OTP.ip_address, value=ip, since=hour_ago
            )
            if per_hour_ip >= settings.ABUSE_RULE_IP_MAX_PER_HOUR:
                return AbuseDecision(
                    False,
                    f"rate_limit_ip_hour: {per_hour_ip}>={settings.ABUSE_RULE_IP_MAX_PER_HOUR}",
                    "rate",
                )

        # --- 3. ML anomaly score (scaffold) ---
        score = anomaly_score(
            {
                "reqs_last_hour": float(per_hour),
                "reqs_last_day": float(per_day),
                "hour_of_day": float(now.hour),
            }
        )
        if score >= settings.ABUSE_ML_SCORE_THRESHOLD:
            return AbuseDecision(
                False, f"anomaly_score: {score:.2f}", "ml", score
            )

        return AbuseDecision(True, None, None, score)

    except Exception:  # noqa: BLE001 — fail open, but loudly
        logger.exception(
            "abuse.evaluate_error",
            extra={"recipient": recipient, "ip": ip},
        )
        # Fail OPEN: a broken abuse engine must not take down 2FA delivery.
        return AbuseDecision(True, "engine_error_fail_open", None, 0.0)