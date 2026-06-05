"""
End-to-end OTP flow — real Postgres (testcontainers), real API-key auth.

Skipped by default; opt in with --run-integration.

Exercises the full HTTP stack:
  create API key → POST /otp/request (with key) → row in DB
  → POST /otp/verify (happy path) → verified
plus the auth failure paths (no key, revoked key).

[LEARN]
We override two dependencies for the test:
  • get_session  → bind the app to the testcontainers engine instead of
                   the module-level engine (which points at the dev DB).
  • dispatch_otp.delay → capture the plaintext code instead of enqueuing
                   to Celery/Redis. The plaintext never crosses HTTP, so
                   intercepting the dispatch call is the only way the test
                   can learn the code to verify it.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.database import get_session
from app.main import create_app
from app.services import api_key_service

pytestmark = pytest.mark.integration


@pytest.fixture
async def e2e(db_engine, monkeypatch):
    """
    Wire a fresh app to the container DB, intercept dispatch, and expose:
      - client:        authless HTTP client
      - session_factory: sessions bound to the container engine
      - sent:          list capturing (otp_id, plaintext) from dispatch
    """
    from sqlalchemy import text

    # Clean slate each test.
    async with db_engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE otps, api_keys RESTART IDENTITY CASCADE"))

    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    async def _override_get_session():
        async with session_factory() as s:
            yield s

    # Capture dispatch instead of hitting Celery/Redis.
    sent: list[tuple[str, str]] = []

    import app.api.otp as otp_api

    class _FakeTask:
        @staticmethod
        def delay(otp_id: str, code: str) -> None:
            sent.append((otp_id, code))

    monkeypatch.setattr(otp_api, "dispatch_otp", _FakeTask)

    app = create_app()
    app.dependency_overrides[get_session] = _override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {"client": client, "session_factory": session_factory, "sent": sent}

    app.dependency_overrides.clear()


async def _make_key(session_factory, name: str = "e2e") -> str:
    async with session_factory() as s:
        _, plaintext = await api_key_service.create_api_key(s, name=name)
    return plaintext


# =============================================================================
# Happy path
# =============================================================================

class TestE2EHappyPath:

    async def test_request_then_verify(self, e2e):
        client = e2e["client"]
        key = await _make_key(e2e["session_factory"])
        headers = {"X-API-Key": key}

        # --- request ---
        resp = await client.post(
            "/otp/request",
            json={"channel": "sms", "recipient": "+21620001000"},
            headers=headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "pending"
        assert body["channel"] == "sms"
        assert "code" not in body  # plaintext never returned over HTTP

        # Dispatch was invoked exactly once; grab the plaintext it captured.
        assert len(e2e["sent"]) == 1
        _otp_id, code = e2e["sent"][0]

        # --- verify ---
        resp = await client.post(
            "/otp/verify",
            json={"recipient": "+21620001000", "code": code},
            headers=headers,
        )
        assert resp.status_code == 200
        vbody = resp.json()
        assert vbody["verified"] is True
        assert vbody["status"] == "verified"

    async def test_verify_wrong_code(self, e2e):
        client = e2e["client"]
        key = await _make_key(e2e["session_factory"])
        headers = {"X-API-Key": key}

        await client.post(
            "/otp/request",
            json={"channel": "sms", "recipient": "+21620001001"},
            headers=headers,
        )
        resp = await client.post(
            "/otp/verify",
            json={"recipient": "+21620001001", "code": "000000"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["verified"] is False


# =============================================================================
# Auth failures
# =============================================================================

class TestE2EAuth:

    async def test_unknown_key_is_401(self, e2e):
        client = e2e["client"]
        resp = await client.post(
            "/otp/request",
            json={"channel": "sms", "recipient": "+21620001002"},
            headers={"X-API-Key": "l2t_totally-bogus-key"},
        )
        assert resp.status_code == 401

    async def test_revoked_key_is_401(self, e2e):
        client = e2e["client"]
        session_factory = e2e["session_factory"]

        # Create then revoke a key.
        async with session_factory() as s:
            row, plaintext = await api_key_service.create_api_key(s, name="to-revoke")
            await api_key_service.revoke(s, row.id)

        resp = await client.post(
            "/otp/request",
            json={"channel": "sms", "recipient": "+21620001003"},
            headers={"X-API-Key": plaintext},
        )
        assert resp.status_code == 401

    async def test_valid_key_updates_last_used(self, e2e):
        from app.models.api_key import APIKey
        from sqlalchemy import select

        client = e2e["client"]
        session_factory = e2e["session_factory"]
        key = await _make_key(session_factory, name="touch")

        await client.post(
            "/otp/request",
            json={"channel": "sms", "recipient": "+21620001004"},
            headers={"X-API-Key": key},
        )

        async with session_factory() as s:
            row = (await s.execute(select(APIKey).where(APIKey.name == "touch"))).scalar_one()
            assert row.last_used_at is not None