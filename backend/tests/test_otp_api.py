"""
OTP HTTP boundary — schema-validation tests (no DB).

Validates that FastAPI rejects malformed requests with 422 *before* the
service layer is invoked. Happy-path POST /otp/request reaches the DB,
so it's deferred to Week 3 with testcontainers-postgres.
"""

from __future__ import annotations

import pytest


class TestOTPRequestValidation:

    async def test_rejects_missing_channel(self, client):
        resp = await client.post("/otp/request", json={"recipient": "+21620000000"})
        assert resp.status_code == 422

    async def test_rejects_invalid_channel(self, client):
        resp = await client.post(
            "/otp/request",
            json={"channel": "carrier-pigeon", "recipient": "+21620000000"},
        )
        assert resp.status_code == 422

    async def test_rejects_non_e164_phone_for_sms(self, client):
        resp = await client.post(
            "/otp/request",
            json={"channel": "sms", "recipient": "not-a-phone"},
        )
        assert resp.status_code == 422

    async def test_rejects_phone_for_email_channel(self, client):
        resp = await client.post(
            "/otp/request",
            json={"channel": "email", "recipient": "+21620000000"},
        )
        assert resp.status_code == 422

    async def test_rejects_extra_field(self, client):
        # extra="forbid" → unknown fields → 422
        resp = await client.post(
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
    async def test_rejects_bad_code_shape(self, client, code: str):
        resp = await client.post(
            "/otp/verify",
            json={"recipient": "+21620000000", "code": code},
        )
        assert resp.status_code == 422

    async def test_rejects_missing_recipient(self, client):
        resp = await client.post("/otp/verify", json={"code": "123456"})
        assert resp.status_code == 422