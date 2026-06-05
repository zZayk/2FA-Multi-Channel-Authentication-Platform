"""
OTP service — DB-backed integration tests.

Skipped by default. Opt in:
    pytest --run-integration
or, inside the dev container:
    docker compose run --rm api pytest --run-integration

Covers `create_otp` + `verify_otp` against a real Postgres container,
including the OTP_STATUS enum and the postgresql.UUID column types that
SQLite can't represent.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.otp import OTP, OTPChannel, OTPStatus
from app.services import otp_service

pytestmark = pytest.mark.integration


# =============================================================================
# create_otp
# =============================================================================

class TestCreateOtp:

    async def test_persists_row_with_hash_not_plaintext(self, db_session):
        otp, code = await otp_service.create_otp(
            db_session,
            channel=OTPChannel.SMS,
            recipient="+21620000001",
        )
        assert otp.id is not None
        assert otp.status is OTPStatus.PENDING
        assert otp.channel is OTPChannel.SMS
        assert otp.code_hash != code
        assert len(otp.code_hash) == 64  # sha256 hex
        # Plaintext returned exactly once.
        assert code.isdigit()
        assert len(code) == 6

    async def test_correlation_id_defaults_when_omitted(self, db_session):
        otp, _ = await otp_service.create_otp(
            db_session, channel=OTPChannel.SMS, recipient="+21620000002"
        )
        assert isinstance(otp.correlation_id, uuid.UUID)

    async def test_correlation_id_honors_caller_supplied(self, db_session):
        cid = uuid.uuid4()
        otp, _ = await otp_service.create_otp(
            db_session,
            channel=OTPChannel.EMAIL,
            recipient="user@example.com",
            correlation_id=cid,
        )
        assert otp.correlation_id == cid

    async def test_expires_at_in_future(self, db_session):
        otp, _ = await otp_service.create_otp(
            db_session, channel=OTPChannel.SMS, recipient="+21620000003"
        )
        assert otp.expires_at > datetime.now(timezone.utc)


# =============================================================================
# verify_otp
# =============================================================================

class TestVerifyOtp:

    async def test_happy_path_marks_verified_and_used(self, db_session):
        otp, code = await otp_service.create_otp(
            db_session, channel=OTPChannel.SMS, recipient="+21620000010"
        )
        outcome = await otp_service.verify_otp(
            db_session, recipient="+21620000010", code=code
        )
        assert outcome.verified is True
        assert outcome.status is OTPStatus.VERIFIED

        # Re-read row.
        refreshed = await db_session.get(OTP, otp.id)
        assert refreshed.status is OTPStatus.VERIFIED
        assert refreshed.used_at is not None

    async def test_wrong_code_increments_attempt_count(self, db_session):
        _, code = await otp_service.create_otp(
            db_session, channel=OTPChannel.SMS, recipient="+21620000011"
        )
        for i in range(3):
            outcome = await otp_service.verify_otp(
                db_session, recipient="+21620000011", code="000000"
            )
            assert outcome.verified is False

        # 4th wrong → still active (max_attempts default = 5).
        assert outcome.attempts_remaining > 0
        assert outcome.status is not OTPStatus.EXPIRED

    async def test_max_attempts_lockout(self, db_session):
        _, _ = await otp_service.create_otp(
            db_session, channel=OTPChannel.SMS, recipient="+21620000012"
        )
        # Default MAX_OTP_ATTEMPTS=5 → 5th wrong attempt should lock out.
        for _i in range(5):
            outcome = await otp_service.verify_otp(
                db_session, recipient="+21620000012", code="000000"
            )
        assert outcome.verified is False
        assert outcome.attempts_remaining == 0
        assert outcome.status is OTPStatus.EXPIRED

        # Correct code after lockout still rejected.
        outcome = await otp_service.verify_otp(
            db_session, recipient="+21620000012", code="123456"
        )
        assert outcome.verified is False
        assert outcome.status is OTPStatus.EXPIRED

    async def test_no_otp_for_recipient(self, db_session):
        outcome = await otp_service.verify_otp(
            db_session, recipient="+21699999999", code="123456"
        )
        assert outcome.verified is False
        assert outcome.attempts_remaining == 0
        assert outcome.status is OTPStatus.EXPIRED

    async def test_ttl_expiry(self, db_session):
        otp, code = await otp_service.create_otp(
            db_session, channel=OTPChannel.SMS, recipient="+21620000013"
        )
        # Force the row past TTL by editing expires_at directly.
        otp.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db_session.commit()

        outcome = await otp_service.verify_otp(
            db_session, recipient="+21620000013", code=code
        )
        assert outcome.verified is False
        assert outcome.status is OTPStatus.EXPIRED

    async def test_single_use_blocks_replay(self, db_session):
        _, code = await otp_service.create_otp(
            db_session, channel=OTPChannel.SMS, recipient="+21620000014"
        )
        first = await otp_service.verify_otp(
            db_session, recipient="+21620000014", code=code
        )
        assert first.verified is True

        # _latest_active_otp returns only non-terminal rows; after VERIFIED,
        # a replay should see no active OTP → EXPIRED outcome.
        second = await otp_service.verify_otp(
            db_session, recipient="+21620000014", code=code
        )
        assert second.verified is False
        assert second.status is OTPStatus.EXPIRED

    async def test_latest_active_only(self, db_session):
        # Two OTPs in quick succession — verify must target the most recent.
        _, code_old = await otp_service.create_otp(
            db_session, channel=OTPChannel.SMS, recipient="+21620000015"
        )
        await asyncio.sleep(0.05)  # ensure created_at differs
        _, code_new = await otp_service.create_otp(
            db_session, channel=OTPChannel.SMS, recipient="+21620000015"
        )

        # Older code should NOT verify against the latest row.
        miss = await otp_service.verify_otp(
            db_session, recipient="+21620000015", code=code_old
        )
        assert miss.verified is False

        hit = await otp_service.verify_otp(
            db_session, recipient="+21620000015", code=code_new
        )
        assert hit.verified is True