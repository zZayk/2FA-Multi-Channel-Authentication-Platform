"""anti-abuse: otp.ip_address + blacklist table

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-20

Month-2 Slice 3 (anti-abuse engine):
  - otps.ip_address : client IP for per-IP rate rules + future GeoIP
  - blacklist       : denylist of recipients / IPs the engine short-circuits
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- otps.ip_address ---
    op.add_column("otps", sa.Column("ip_address", sa.String(length=45), nullable=True))
    op.create_index("ix_otps_ip_address", "otps", ["ip_address"])

    # --- blacklist ---
    blacklist_kind = postgresql.ENUM(
        "recipient", "ip", name="blacklist_kind", create_type=True
    )
    op.create_table(
        "blacklist",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column("kind", blacklist_kind, nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Matches the model's `unique=True, index=True` → one unique index.
    op.create_index("ix_blacklist_value", "blacklist", ["value"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_blacklist_value", table_name="blacklist")
    op.drop_table("blacklist")
    sa.Enum(name="blacklist_kind").drop(op.get_bind(), checkfirst=True)

    op.drop_index("ix_otps_ip_address", table_name="otps")
    op.drop_column("otps", "ip_address")