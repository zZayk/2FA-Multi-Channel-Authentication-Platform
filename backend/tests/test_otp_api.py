"""
OTP HTTP boundary — auth + schema-validation tests (no DB).

Two concerns covered here:
  1. Auth gate: requests without a valid X-API-Key get 401 (uses `client`).
  2. Schema validation: malformed bodies get 422 *after* auth passes
     (uses `auth_client`, which overrides the API-key dependency).

Happy-path POST /otp/request reaches the DB → covered by the E2E
integration test (test_e2e_otp.py), not here.
"""

from __future__ import annotations

import pytest


# =============================================================================
# Auth gate — no DB needed (missing-key path returns before any query)
# =============================================================================

class TestAuthGate:

    async def test_request_without_key_is_401(self, client):
        resp = await client.post(
            "/otp/request",
            json={"channel": "sms", "recipient": "+21620000000"},
        )
        assert resp.status_code == 401

    async def test_verify_without_key_is_401(self, client):
        resp = await client.post(
            "/otp/verify",
            json={"recipient": "+21620000000", "code": "123456"},
        )
        assert resp.status_code == 401

    async def test_401_sets_www_authenticate_header(self, client):
        resp = await client.post(
            "/otp/request",
            json={"channel": "sms", "recipient": "+21620000000"},
        )
        assert resp.headers.get("WWW-Authenticate") == "ApiKey"


# =============================================================================
# Request schema validation — auth overridden, so we reach body validation
# =============================================================================

class TestOTPRequestValidation:

    async def test_rejects_missing_channel(self, auth_client):
        resp = await auth_client.post("/otp/request", json={"recipient": "+21620000000"})
        assert resp.status_code == 422

    async def test_rejects_invalid_channel(self, auth_client):
        resp = await auth_client.post(
            "/otp/request",
            json={"channel": "carrier-pigeon", "recipient": "+21620000000"},
        )
        assert resp.status_code == 422

    async def test_rejects_non_e164_phone_for_sms(self, auth_client):
        resp = await auth_client.post(
            "/otp/request",
            json={"channel": "sms", "recipient": "not-a-phone"},
        )
        assert resp.status_code == 422

    async def test_rejects_phone_for_email_channel(self, auth_client):
        resp = await auth_client.post(
            "/otp/request",
            json={"channel": "email", "recipient": "+21620000000"},
        )
        assert resp.status_code == 422

    async def test_rejects_extra_field(self, auth_client):
        resp = await auth_client.post(
            "/otp/request",
            json={
                "channel": "sms",
                "recipient": "+21620000000",
                "secret_admin_flag": True,
            },
        )
        assert resp.status_code == 422


class TestOTPVerifyValidation:

    @pytest.mark.parametrize("code", ["abc", "12", "12345678901", "12a456"])
    async def test_rejects_bad_code_shape(self, auth_client, code: str):
        resp = await auth_client.post(
            "/otp/verify",
            json={"recipient": "+21620000000", "code": code},
        )
        assert resp.status_code == 422

    async def test_rejects_missing_recipient(self, auth_client):
        resp = await auth_client.post("/otp/verify", json={"code": "123456"})
        assert resp.status_code == 422