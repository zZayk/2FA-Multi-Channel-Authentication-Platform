"""otp DLR fields: provider_message_id + delivery timestamps

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-10

Adds the columns the Month-2 DLR poll needs:
  - provider_message_id : lookup key for TunisiaSMS delivery receipts
  - sent_at / delivered_at / failed_at : per-transition timestamps feeding
    delivery-latency + delivery-rate metrics (Slice 2)

Plus an index supporting the poll's hot query
("in-flight SMS rows, oldest first").
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "otps",
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
    )
    op.add_column("otps", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "otps", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "otps", sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_index(
        "ix_otps_provider_message_id", "otps", ["provider_message_id"]
    )
    # Supports the DLR poll: WHERE status = 'sent' ORDER BY sent_at.
    op.create_index("ix_otp_status_sent_at", "otps", ["status", "sent_at"])


def downgrade() -> None:
    op.drop_index("ix_otp_status_sent_at", table_name="otps")
    op.drop_index("ix_otps_provider_message_id", table_name="otps")
    op.drop_column("otps", "failed_at")
    op.drop_column("otps", "delivered_at")
    op.drop_column("otps", "sent_at")
    op.drop_column("otps", "provider_message_id")