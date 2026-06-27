"""
DLR (delivery receipt) tests — Month 2, Slice 1.

Two layers:
  • TestQueryDlr     — unit. The SMS adapter's HTTP→DlrStatus mapping, with
                       httpx faked (no network). Runs in the default suite.
  • TestPollPending  — integration. The reconciliation loop against a real
                       Postgres (testcontainers). Opt-in: --run-integration.

[LEARN]
We fake at two different seams on purpose:
  - adapter tests fake `httpx.AsyncClient` → exercise the real status-map code.
  - poll tests fake `sms_adapter.query_dlr` → exercise the real DB state
    machine without caring how the provider HTTP looks.
Each test isolates one layer; we never need a live provider OR a live DB at
the same time.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.models.otp import OTPChannel, OTPStatus
from app.services.channels.base import DlrStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# =============================================================================
# Unit — SMS adapter query_dlr HTTP mapping
# =============================================================================

class _FakeAsyncClient:
    """
    Drop-in for httpx.AsyncClient as an async context manager.

    Returns a pre-baked Response from `.get()`, or raises a pre-baked
    exception — lets us drive every branch of query_dlr without a network.
    """

    def __init__(self, *, response: httpx.Response | None = None, exc: Exception | None = None):
        self._response = response
        self._exc = exc

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    async def get(self, url, headers=None):  # noqa: ANN001 — test fake
        if self._exc is not None:
            raise self._exc
        return self._response


def _patch_client(monkeypatch, *, response=None, exc=None) -> None:
    """Make `httpx.AsyncClient(...)` inside the sms module return our fake."""
    import app.services.channels.sms as sms_mod

    monkeypatch.setattr(
        sms_mod.httpx,
        "AsyncClient",
        lambda **kw: _FakeAsyncClient(response=response, exc=exc),
    )


class TestQueryDlr:

    @pytest.mark.parametrize(
        "raw_status,expected",
        [
            ("DELIVRD", DlrStatus.DELIVERED),
            ("delivered", DlrStatus.DELIVERED),
            ("UNDELIV", DlrStatus.FAILED),
            ("REJECTED", DlrStatus.FAILED),
            ("EXPIRED", DlrStatus.FAILED),
            ("QUEUED", DlrStatus.PENDING),
            ("ENROUTE", DlrStatus.PENDING),
            ("WAT", DlrStatus.UNKNOWN),   # unmapped token → fail-safe UNKNOWN
        ],
    )
    async def test_status_mapping(self, monkeypatch, raw_status, expected):
        from app.services.channels.sms import sms_adapter

        resp = httpx.Response(200, json={"status": raw_status})
        _patch_client(monkeypatch, response=resp)

        result = await sms_adapter.query_dlr(
            provider_message_id="msg-1", correlation_id=uuid.uuid4()
        )
        assert result.status is expected

    async def test_404_is_unknown(self, monkeypatch):
        from app.services.channels.sms import sms_adapter

        _patch_client(monkeypatch, response=httpx.Response(404))
        result = await sms_adapter.query_dlr(
            provider_message_id="gone", correlation_id=uuid.uuid4()
        )
        assert result.status is DlrStatus.UNKNOWN

    async def test_5xx_stays_pending(self, monkeypatch):
        from app.services.channels.sms import sms_adapter

        _patch_client(monkeypatch, response=httpx.Response(503))
        result = await sms_adapter.query_dlr(
            provider_message_id="msg-1", correlation_id=uuid.uuid4()
        )
        # Server blip must NOT be read as terminal — retry next tick.
        assert result.status is DlrStatus.PENDING

    async def test_network_error_stays_pending(self, monkeypatch):
        from app.services.channels.sms import sms_adapter

        _patch_client(monkeypatch, exc=httpx.ConnectError("boom"))
        result = await sms_adapter.query_dlr(
            provider_message_id="msg-1", correlation_id=uuid.uuid4()
        )
        assert result.status is DlrStatus.PENDING

    async def test_email_adapter_has_no_dlr(self):
        from app.services.channels.email import email_adapter

        with pytest.raises(NotImplementedError):
            await email_adapter.query_dlr(
                provider_message_id="x", correlation_id=uuid.uuid4()
            )


# =============================================================================
# Integration — poll_pending reconciliation loop
# =============================================================================


@pytest.fixture
async def poll_env(db_engine, monkeypatch):
    """
    Clean DB + dlr.SessionLocal bound to the container + a programmable fake
    SMS DLR endpoint. Exposes a session factory and a `dlr_map` the test fills
    with provider_message_id → DlrStatus.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async with db_engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE otps RESTART IDENTITY CASCADE"))

    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    import app.tasks.dlr as dlr_mod

    # Point the task's session factory at the container engine.
    monkeypatch.setattr(dlr_mod, "TaskSessionLocal", session_factory)

    # Program the SMS adapter's DLR responses + record which ids were queried.
    from app.services.channels.base import DlrResult
    from app.services.channels.sms import sms_adapter

    dlr_map: dict[str, DlrStatus] = {}
    queried: list[str] = []

    async def _fake_query_dlr(*, provider_message_id, correlation_id):
        queried.append(provider_message_id)
        return DlrResult(status=dlr_map.get(provider_message_id, DlrStatus.PENDING))

    monkeypatch.setattr(sms_adapter, "query_dlr", _fake_query_dlr)

    yield {"factory": session_factory, "dlr_map": dlr_map, "queried": queried}


async def _seed_otp(
    factory,
    *,
    status=OTPStatus.SENT,
    channel=OTPChannel.SMS,
    recipient="+21620002000",
    provider_message_id="msg-x",
    sent_age_seconds=10,
):
    from app.models.otp import OTP

    async with factory() as s:
        otp = OTP(
            id=uuid.uuid4(),
            code_hash="0" * 64,
            channel=channel,
            recipient=recipient,
            status=status,
            expires_at=_utcnow() + timedelta(minutes=5),
            sent_at=_utcnow() - timedelta(seconds=sent_age_seconds),
            provider_message_id=provider_message_id,
        )
        s.add(otp)
        await s.commit()
        return otp.id


@pytest.mark.integration
class TestPollPending:

    async def test_delivered_transition(self, poll_env):
        from app.models.otp import OTP
        from app.tasks.dlr import _poll_pending_async

        otp_id = await _seed_otp(poll_env["factory"], provider_message_id="d1")
        poll_env["dlr_map"]["d1"] = DlrStatus.DELIVERED

        summary = await _poll_pending_async()
        assert summary["delivered"] == 1

        async with poll_env["factory"]() as s:
            row = await s.get(OTP, otp_id)
            assert row.status is OTPStatus.DELIVERED
            assert row.delivered_at is not None

    async def test_failed_transition(self, poll_env):
        from app.models.otp import OTP
        from app.tasks.dlr import _poll_pending_async

        otp_id = await _seed_otp(poll_env["factory"], provider_message_id="f1")
        poll_env["dlr_map"]["f1"] = DlrStatus.FAILED

        summary = await _poll_pending_async()
        assert summary["failed"] == 1

        async with poll_env["factory"]() as s:
            row = await s.get(OTP, otp_id)
            assert row.status is OTPStatus.FAILED
            assert row.failed_at is not None

    async def test_pending_stays_sent(self, poll_env):
        from app.models.otp import OTP
        from app.tasks.dlr import _poll_pending_async

        otp_id = await _seed_otp(poll_env["factory"], provider_message_id="p1")
        poll_env["dlr_map"]["p1"] = DlrStatus.PENDING

        summary = await _poll_pending_async()
        assert summary["pending"] == 1

        async with poll_env["factory"]() as s:
            row = await s.get(OTP, otp_id)
            assert row.status is OTPStatus.SENT  # unchanged — still in flight

    async def test_timeout_sweep_fails_without_dlr_call(self, poll_env):
        from app.models.otp import OTP
        from app.tasks.dlr import _poll_pending_async

        # Sent 700s ago; default DLR_TIMEOUT_SECONDS=600 → swept.
        otp_id = await _seed_otp(
            poll_env["factory"], provider_message_id="old1", sent_age_seconds=700
        )

        summary = await _poll_pending_async()
        assert summary["timed_out"] == 1
        # The DLR endpoint must NOT have been called for a timed-out row.
        assert "old1" not in poll_env["queried"]

        async with poll_env["factory"]() as s:
            row = await s.get(OTP, otp_id)
            assert row.status is OTPStatus.FAILED

    async def test_email_rows_not_polled(self, poll_env):
        from app.models.otp import OTP
        from app.tasks.dlr import _poll_pending_async

        otp_id = await _seed_otp(
            poll_env["factory"],
            channel=OTPChannel.EMAIL,
            recipient="u@example.com",
            provider_message_id="e1",
        )
        summary = await _poll_pending_async()
        assert summary["polled"] == 0

        async with poll_env["factory"]() as s:
            row = await s.get(OTP, otp_id)
            assert row.status is OTPStatus.SENT  # untouched

    async def test_non_sent_rows_ignored(self, poll_env):
        from app.tasks.dlr import _poll_pending_async

        await _seed_otp(
            poll_env["factory"], status=OTPStatus.DELIVERED, provider_message_id="done1"
        )
        summary = await _poll_pending_async()
        assert summary["polled"] == 0