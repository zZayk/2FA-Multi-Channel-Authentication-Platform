"""
OTP service — owns OTP lifecycle (create + verify) and security invariants.

[LEARN]
Pattern: "Service layer / Application service".
The service is the only place that:
  - generates the plaintext code (in memory, returned once for delivery)
  - hashes the code for storage (`bcrypt`)
  - enforces TTL, single-use, max-attempts on verify
  - commits the transaction
Routers (api/) parse HTTP and call service functions; they don't touch SQL.
Channel adapters (services/sms.py, services/email.py — Week 3) consume the
plaintext code returned by `create_otp` and never see the hash.

Why the service commits, not the router:
  Business operation = one transaction. Service knows when the invariant
  is satisfied; router does not. Pushing commit to router risks half-applied
  state (e.g. OTP row written but attempt_count not incremented).

Read more:
  - Martin Fowler — "Anemic Domain Model" antipattern
  - OWASP Authentication Cheat Sheet, §"Implement Proper Password Strength Controls"
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.otp import OTP, OTPChannel, OTPStatus


# =============================================================================
# Crypto helpers
# =============================================================================
# [LEARN] Why HMAC-SHA256 instead of bcrypt for OTPs?
#   bcrypt's slow work factor exists to defend against offline brute-force of
#   *reused, human-chosen passwords*. OTP codes are:
#     - 6-digit, CSPRNG-generated (not human-chosen)
#     - Short-lived (minutes TTL)
#     - Single-use with max-attempts lockout enforced at the API layer
#   An attacker with the DB still can't brute-force all 1 000 000 codes
#   without also owning SECRET_KEY. HMAC-SHA256 is correct; bcrypt was overkill
#   AND broke with bcrypt >=4.x (removed __about__, 72-byte limit in detect_wrap_bug).
#   Read more: OWASP Authentication Cheat Sheet §OTP Storage


def generate_code(length: int | None = None) -> str:
    """
    Crypto-secure numeric OTP, zero-padded to `length`.

    [LEARN]
    Uses `secrets.randbelow(10**N)` — backed by the OS CSPRNG. Never use
    `random.randint`; the `random` module is a Mersenne Twister, predictable
    after observing enough outputs. For TOTP/HOTP (authenticator apps) the
    appropriate primitive is `pyotp.TOTP` — different use case from
    one-shot delivery codes.
    """
    n = length or get_settings().OTP_CODE_LENGTH
    upper = 10**n
    return f"{secrets.randbelow(upper):0{n}d}"


def hash_code(code: str) -> str:
    """Return HMAC-SHA256 hex digest of the OTP code, keyed with SECRET_KEY."""
    key = get_settings().SECRET_KEY.get_secret_value().encode()
    return hmac.new(key, code.encode(), hashlib.sha256).hexdigest()


def verify_code(code: str, code_hash: str) -> bool:
    """Constant-time compare — prevents timing side-channel."""
    return hmac.compare_digest(hash_code(code), code_hash)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# =============================================================================
# Verify outcome
# =============================================================================

@dataclass(slots=True)
class VerifyOutcome:
    verified: bool
    attempts_remaining: int
    status: OTPStatus


# =============================================================================
# Service operations
# =============================================================================

async def create_otp(
    session: AsyncSession,
    *,
    channel: OTPChannel,
    recipient: str,
    correlation_id: uuid.UUID | None = None,
) -> tuple[OTP, str]:
    """
    Create an OTP row and return (orm_row, plaintext_code).

    The caller (router → Celery task → channel adapter) consumes the plaintext
    once to deliver the message and then discards it. The plaintext NEVER
    leaves this function alive in any persistent form.

    [NEW CONCEPT] "Pre-token / out-of-band delivery code". The plaintext is
    handed back exactly once; thereafter only its HMAC-SHA256 hash exists.
    """
    settings = get_settings()
    code = generate_code(settings.OTP_CODE_LENGTH)

    now = _utcnow()
    otp = OTP(
        code_hash=hash_code(code),
        channel=channel,
        recipient=recipient,
        status=OTPStatus.PENDING,
        expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
        correlation_id=correlation_id or uuid.uuid4(),
        attempt_count=0,
    )
    session.add(otp)
    await session.commit()
    await session.refresh(otp)
    return otp, code


async def _latest_active_otp(
    session: AsyncSession, recipient: str
) -> OTP | None:
    """
    Most recent OTP for this recipient that hasn't reached a terminal state.
    Terminal = VERIFIED, EXPIRED, BLOCKED.
    """
    stmt = (
        select(OTP)
        .where(
            OTP.recipient == recipient,
            OTP.status.in_(
                (
                    OTPStatus.PENDING,
                    OTPStatus.SENT,
                    OTPStatus.DELIVERED,
                    OTPStatus.FAILED,
                )
            ),
        )
        .order_by(OTP.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def verify_otp(
    session: AsyncSession,
    *,
    recipient: str,
    code: str,
) -> VerifyOutcome:
    """
    Validate `code` against the latest active OTP for `recipient`.

    Invariants enforced (in order):
      1. OTP must exist.
      2. OTP must not be past `expires_at` (TTL).
      3. OTP must not be already used (`used_at IS NULL`).
      4. `attempt_count` must be below MAX_OTP_ATTEMPTS *before* this check.
      5. On match: stamp `used_at`, set status VERIFIED, return verified=True.
      6. On miss: increment attempt_count; if over cap → mark EXPIRED.

    All transitions commit before returning.
    """
    settings = get_settings()
    otp = await _latest_active_otp(session, recipient)
    if otp is None:
        return VerifyOutcome(verified=False, attempts_remaining=0, status=OTPStatus.EXPIRED)

    # --- 2. TTL ----------------------------------------------------------
    if _utcnow() >= otp.expires_at:
        otp.status = OTPStatus.EXPIRED
        await session.commit()
        return VerifyOutcome(
            verified=False, attempts_remaining=0, status=OTPStatus.EXPIRED
        )

    # --- 3. Single-use ---------------------------------------------------
    if otp.used_at is not None:
        return VerifyOutcome(
            verified=False, attempts_remaining=0, status=otp.status
        )

    # --- 4. Max attempts (pre-check) ------------------------------------
    if otp.attempt_count >= settings.MAX_OTP_ATTEMPTS:
        otp.status = OTPStatus.EXPIRED
        await session.commit()
        return VerifyOutcome(
            verified=False, attempts_remaining=0, status=OTPStatus.EXPIRED
        )

    # --- 5/6. Compare ---------------------------------------------------
    if verify_code(code, otp.code_hash):
        otp.status = OTPStatus.VERIFIED
        otp.used_at = _utcnow()
        await session.commit()
        return VerifyOutcome(
            verified=True,
            attempts_remaining=settings.MAX_OTP_ATTEMPTS - otp.attempt_count,
            status=OTPStatus.VERIFIED,
        )

    # Miss → increment, possibly lock out.
    otp.attempt_count += 1
    if otp.attempt_count >= settings.MAX_OTP_ATTEMPTS:
        otp.status = OTPStatus.EXPIRED  # reused for "too many attempts"
        attempts_remaining = 0
    else:
        attempts_remaining = settings.MAX_OTP_ATTEMPTS - otp.attempt_count
    await session.commit()
    return VerifyOutcome(
        verified=False, attempts_remaining=attempts_remaining, status=otp.status
    )