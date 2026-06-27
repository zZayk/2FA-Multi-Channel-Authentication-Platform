"""
Anti-abuse engine + Slice-2/3 endpoint tests.

Layers:
  • Unit         — ML stub, decision defaults, schema validation, auth gate.
                   Run in the default suite (no DB).
  • Integration  — engine evaluate() + blacklist + rate rules + metrics against
                   real Postgres. Opt-in: --run-integration.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.models.otp import OTPChannel, OTPStatus
from app.services.abuse.engine import AbuseDecision
from app.services.abuse.ml import NEUTRAL_SCORE, anomaly_score


# =============================================================================
# Unit — ML stub + decision object
# =============================================================================

class TestMlStub:

    def test_anomaly_score_is_neutral(self):
        assert anomaly_score({"reqs_last_hour": 999.0, "hour_of_day": 3.0}) == NEUTRAL_SCORE

    def test_neutral_is_zero(self):
        assert NEUTRAL_SCORE == 0.0


class TestAbuseDecision:

    def test_defaults(self):
        d = AbuseDecision(True)
        assert d.allowed is True
        assert d.reason is None and d.rule is None and d.score == 0.0


# =============================================================================
# Unit — auth gate on new routes (no DB; missing key returns before any query)
# =============================================================================

class TestAuthGate:

    async def test_resend_without_key_is_401(self, client):
        resp = await client.post(
            "/otp/resend", json={"channel": "email", "recipient": "u@x.com"}
        )
        assert resp.status_code == 401

    async def test_metrics_without_key_is_401(self, client):
        assert (await client.get("/otp/metrics")).status_code == 401

    async def test_status_without_key_is_401(self, client):
        assert (await client.get(f"/otp/{uuid.uuid4()}")).status_code == 401

    async def test_blacklist_add_without_key_is_401(self, client):
        resp = await client.post(
            "/admin/blacklist", json={"value": "+21620000000", "kind": "recipient"}
        )
        assert resp.status_code == 401


# =============================================================================
# Unit — schema validation (auth overridden → reaches body validation)
# =============================================================================

class TestResendValidation:

    async def test_rejects_phone_for_email_channel(self, auth_client):
        resp = await auth_client.post(
            "/otp/resend", json={"channel": "email", "recipient": "+21620000000"}
        )
        assert resp.status_code == 422

    async def test_rejects_extra_field(self, auth_client):
        resp = await auth_client.post(
            "/otp/resend",
            json={"channel": "sms", "recipient": "+21620000000", "x": 1},
        )
        assert resp.status_code == 422


class TestBlacklistValidation:

    async def test_rejects_missing_kind(self, auth_client):
        resp = await auth_client.post("/admin/blacklist", json={"value": "+21620000000"})
        assert resp.status_code == 422

    async def test_rejects_bad_kind(self, auth_client):
        resp = await auth_client.post(
            "/admin/blacklist", json={"value": "x", "kind": "spaceship"}
        )
        assert resp.status_code == 422


# =============================================================================
# Integration — engine + endpoints against real Postgres
# =============================================================================

@pytest.mark.integration
class TestEngineEvaluate:

    async def test_allows_clean_recipient(self, db_session):
        from app.services.abuse import evaluate

        d = await evaluate(db_session, recipient="+21620003000", ip="1.2.3.4")
        assert d.allowed is True
        assert d.rule is None

    async def test_blacklisted_recipient_blocked(self, db_session):
        from app.models.blacklist import BlacklistKind
        from app.services.abuse import add_to_blacklist, evaluate

        await add_to_blacklist(
            db_session, value="+21620003001", kind=BlacklistKind.RECIPIENT, reason="test"
        )
        d = await evaluate(db_session, recipient="+21620003001")
        assert d.allowed is False
        assert d.rule == "blacklist"

    async def test_blacklisted_ip_blocked(self, db_session):
        from app.models.blacklist import BlacklistKind
        from app.services.abuse import add_to_blacklist, evaluate

        await add_to_blacklist(db_session, value="9.9.9.9", kind=BlacklistKind.IP)
        d = await evaluate(db_session, recipient="+21620003002", ip="9.9.9.9")
        assert d.allowed is False
        assert d.reason == "blacklisted_ip"

    async def test_hourly_rate_limit_blocks(self, db_session):
        from app.core.config import get_settings
        from app.services import otp_service
        from app.services.abuse import evaluate

        recipient = "+21620003003"
        cap = get_settings().ABUSE_RULE_MAX_PER_HOUR
        for _ in range(cap):
            await otp_service.create_otp(
                db_session, channel=OTPChannel.SMS, recipient=recipient
            )
        d = await evaluate(db_session, recipient=recipient)
        assert d.allowed is False
        assert d.rule == "rate"

    async def test_blocked_audit_rows_dont_compound(self, db_session):
        # record_blocked rows are excluded from rate counting.
        from app.services import otp_service
        from app.services.abuse import evaluate

        recipient = "+21620003004"
        for _ in range(20):
            await otp_service.record_blocked(
                db_session, channel=OTPChannel.SMS, recipient=recipient
            )
        d = await evaluate(db_session, recipient=recipient)
        assert d.allowed is True  # blocked rows not counted

    async def test_remove_from_blacklist(self, db_session):
        from app.models.blacklist import BlacklistKind
        from app.services.abuse import add_to_blacklist, evaluate, remove_from_blacklist

        await add_to_blacklist(db_session, value="+21620003005", kind=BlacklistKind.RECIPIENT)
        assert (await evaluate(db_session, recipient="+21620003005")).allowed is False
        removed = await remove_from_blacklist(db_session, value="+21620003005")
        assert removed is True
        assert (await evaluate(db_session, recipient="+21620003005")).allowed is True


@pytest.mark.integration
class TestDeliveryMetrics:

    async def test_metrics_compute_delivery_rate(self, db_session):
        from app.services import otp_service

        # 2 delivered + 1 failed SMS → rate 2/3.
        for i in range(3):
            otp, _ = await otp_service.create_otp(
                db_session, channel=OTPChannel.SMS, recipient=f"+2162000400{i}"
            )
            otp.sent_at = otp.created_at
            if i < 2:
                otp.status = OTPStatus.DELIVERED
                otp.delivered_at = otp.created_at + timedelta(seconds=5)
            else:
                otp.status = OTPStatus.FAILED
                otp.failed_at = otp.created_at + timedelta(seconds=5)
        await db_session.commit()

        metrics = await otp_service.delivery_metrics(db_session)
        sms = next(m for m in metrics if m.channel is OTPChannel.SMS)
        assert sms.total == 3
        assert sms.delivered == 2
        assert sms.failed == 1
        assert round(sms.delivery_rate, 3) == round(2 / 3, 3)
        assert sms.avg_delivery_seconds == 5.0