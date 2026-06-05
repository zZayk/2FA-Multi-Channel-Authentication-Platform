"""
OTP ORM model — one row per OTP code issued.

[LEARN]
Pattern: "Audit-friendly state machine row".
We never overwrite history — `status` transitions are tracked over time,
`attempt_count` increments per verify attempt, `used_at` stamps single-use
consumption. This shape lets the abuse engine analyse patterns later
(e.g. "5 OTPs created in 10s to the same recipient").

Key security points (DO NOT regress on these):
  • code_hash is HMAC-SHA256 of the OTP, keyed with SECRET_KEY —
    NEVER store the plaintext code. A DB dump alone is useless to an
    attacker (they still need SECRET_KEY to forge a hash). The service
    layer hashes on create and constant-time-compares on verify.
  • expires_at + used_at + attempt_count together enforce TTL,
    single-use, and max-retries — all checked in services/otp_service.py.
  • correlation_id is a UUID that travels with the auth attempt
    (request header → service → Celery task → channel adapter → logs).
    One ID, full trace across processes.

Read more:
  - OWASP Cheat Sheet — Authentication (OTP storage)
  - Google SRE book, ch.16 "Tracing" — concept of correlation/trace IDs
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


# [LEARN] Python Enum mirrored as a Postgres ENUM type.
# Using SQLAlchemy's `Enum(...)` ensures the DB enforces the value set —
# no garbage strings can land in the column, even via raw SQL.
class OTPChannel(str, enum.Enum):
    SMS = "sms"
    EMAIL = "email"


class OTPStatus(str, enum.Enum):
    PENDING = "pending"     # created, not yet sent (queued)
    SENT = "sent"           # accepted by channel adapter
    DELIVERED = "delivered" # confirmed by DLR (SMS) or SMTP 250 (email)
    FAILED = "failed"       # channel adapter rejected / DLR FAILED
    VERIFIED = "verified"   # user supplied correct code in time
    EXPIRED = "expired"     # TTL passed without verify
    BLOCKED = "blocked"     # anti-abuse engine refused to send


def _utcnow() -> datetime:
    """UTC-aware now() — never use naive datetimes for timestamps."""
    return datetime.now(timezone.utc)


class OTP(Base):
    __tablename__ = "otps"

    # Primary key — UUID, not auto-increment integer.
    # [LEARN] UUIDs prevent enumeration attacks (attacker can't iterate /otp/1, /otp/2...)
    # and are safe to expose in logs / URLs.
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Hashed OTP (HMAC-SHA256 hex, 64 chars) — NEVER store the plaintext value.
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    channel: Mapped[OTPChannel] = mapped_column(
        Enum(OTPChannel, name="otp_channel", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )

    # Recipient — phone (E.164) for SMS, email for EMAIL.
    # No FK to a User table yet; this service may run standalone.
    recipient: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    status: Mapped[OTPStatus] = mapped_column(
        Enum(OTPStatus, name="otp_status", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=OTPStatus.PENDING,
    )

    # Lifecycle timestamps — all UTC.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Verify attempts — service caps at settings.MAX_OTP_ATTEMPTS.
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Trace ID — flows api → service → task → channel adapter → DB → logs.
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        default=uuid.uuid4,
        index=True,
    )

    __table_args__ = (
        # Common query: "active OTPs for this recipient" — covered by composite index.
        Index("ix_otp_recipient_status", "recipient", "status"),
        Index("ix_otp_created_at", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<OTP id={self.id} channel={self.channel.value} "
            f"recipient={self.recipient!r} status={self.status.value}>"
        )
