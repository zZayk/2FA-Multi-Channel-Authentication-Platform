"""
Blacklist ORM model — denylist of recipients / IPs the abuse engine blocks.

[LEARN]
Pattern: "Denylist short-circuit". The anti-abuse engine checks this table
FIRST, before any rate-rule or ML scoring. A hit is a hard, explainable block
(O(1) indexed lookup) — no point scoring a known-bad actor. Entries are keyed
by a normalized `value` (the phone/email or the IP string) plus a `kind` so a
phone and an IP can't collide.

Why a table, not a Redis set, for v1:
  Durability + auditability (who/why/when) matter more than microseconds here;
  blocks are rare relative to sends. A Redis cache layer can front this later
  if lookups ever get hot.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BlacklistKind(str, enum.Enum):
    RECIPIENT = "recipient"  # a phone (E.164) or email address
    IP = "ip"                # a client IP address


class BlacklistEntry(Base):
    __tablename__ = "blacklist"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # The normalized value being blocked. Unique so re-adding is idempotent at
    # the DB level and lookups are a single indexed equality probe.
    value: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    kind: Mapped[BlacklistKind] = mapped_column(
        Enum(BlacklistKind, name="blacklist_kind", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )

    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BlacklistEntry {self.kind.value}={self.value!r}>"