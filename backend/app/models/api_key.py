"""
APIKey ORM model — one row per issued API key.

[LEARN]
Same storage pattern as OTPs: HMAC-SHA256 of the key keyed with
SECRET_KEY. The DB never sees the plaintext key — a dump alone cannot
be replayed against the API without also stealing SECRET_KEY.

`key_prefix` is the first 8 chars of the plaintext (e.g. "l2t_abc1") —
safe to log + show in admin UI. Lets ops identify a key in incident
reviews without revealing the secret.

Lifecycle:
  created_at   — issued
  last_used_at — refreshed on each successful auth (debounced; updated
                 at most once per N seconds in service layer to avoid
                 write amplification on hot keys)
  revoked_at   — set when ops kills the key; auth rejects rows where
                 this is non-NULL
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Human-readable label — "frontend-dev", "billing-cron", etc.
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # HMAC-SHA256 hex digest of the plaintext key (64 chars).
    key_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )

    # First 8 chars of plaintext — safe to display/log.
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # Active-keys query covered by partial-ish index on revoked_at.
        Index("ix_api_keys_revoked_at", "revoked_at"),
    )

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<APIKey id={self.id} name={self.name!r} "
            f"prefix={self.key_prefix!r} active={self.is_active}>"
        )