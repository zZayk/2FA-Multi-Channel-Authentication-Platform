"""init otp table

Revision ID: 0001
Revises:
Create Date: 2026-05-25

[LEARN]
First migration is handcrafted (not `alembic revision --autogenerate`).
Why: autogenerate output is verbose and order-dependent; reading a clean
handwritten upgrade()/downgrade() is the best way to learn the API.
Subsequent migrations should use autogenerate as a starting point, then
hand-edit for clarity.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None


# Enum values must match app.models.otp.OTPChannel / OTPStatus exactly.
OTP_CHANNEL_VALUES = ("sms", "email")
OTP_STATUS_VALUES = (
    "pending",
    "sent",
    "delivered",
    "failed",
    "verified",
    "expired",
    "blocked",
)


def upgrade() -> None:
    # --- ENUM types ---------------------------------------------------------
    # create_type=False on the Column means we manage the type lifecycle here
    # explicitly, so downgrade() can drop it cleanly.
    otp_channel = postgresql.ENUM(*OTP_CHANNEL_VALUES, name="otp_channel")
    otp_status = postgresql.ENUM(*OTP_STATUS_VALUES, name="otp_status")
    otp_channel.create(op.get_bind(), checkfirst=True)
    otp_status.create(op.get_bind(), checkfirst=True)

    # --- otps table ---------------------------------------------------------
    op.create_table(
        "otps",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "channel",
            postgresql.ENUM(*OTP_CHANNEL_VALUES, name="otp_channel", create_type=False),
            nullable=False,
        ),
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(*OTP_STATUS_VALUES, name="otp_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "correlation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
    )

    # --- Indexes ------------------------------------------------------------
    op.create_index("ix_otps_recipient", "otps", ["recipient"])
    op.create_index("ix_otps_correlation_id", "otps", ["correlation_id"])
    op.create_index("ix_otp_recipient_status", "otps", ["recipient", "status"])
    op.create_index("ix_otp_created_at", "otps", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_otp_created_at", table_name="otps")
    op.drop_index("ix_otp_recipient_status", table_name="otps")
    op.drop_index("ix_otps_correlation_id", table_name="otps")
    op.drop_index("ix_otps_recipient", table_name="otps")
    op.drop_table("otps")

    # Drop enum types last, after the column referencing them is gone.
    postgresql.ENUM(name="otp_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="otp_channel").drop(op.get_bind(), checkfirst=True)