"""
OTP service — pure helper tests.

DB-backed tests (create_otp / verify_otp) land in Week 3 with
testcontainers-postgres (substep G).
"""

from __future__ import annotations

import pytest

from app.services.otp_service import (
    generate_code,
    hash_code,
    verify_code,
)


# =============================================================================
# generate_code
# =============================================================================

class TestGenerateCode:
    def test_default_length_matches_setting(self):
        code = generate_code()
        assert len(code) == 6  # OTP_CODE_LENGTH default
        assert code.isdigit()

    @pytest.mark.parametrize("n", [4, 5, 6, 8, 10])
    def test_custom_length_pads_zeros(self, n: int):
        for _ in range(50):
            code = generate_code(n)
            assert len(code) == n
            assert code.isdigit()

    def test_codes_change_between_calls(self):
        # Crypto RNG: collisions on 6 digits ~1/10^6.
        # 100 calls → expect >95 distinct values.
        codes = {generate_code(6) for _ in range(100)}
        assert len(codes) > 95


# =============================================================================
# hash_code / verify_code — HMAC-SHA256, keyed with SECRET_KEY
# =============================================================================

class TestHmacRoundtrip:
    def test_hash_is_not_plaintext(self):
        h = hash_code("123456")
        assert "123456" not in h
        assert len(h) == 64  # sha256 hex digest

    def test_hash_is_hex(self):
        h = hash_code("123456")
        int(h, 16)  # raises if not valid hex

    def test_verify_correct_code(self):
        h = hash_code("123456")
        assert verify_code("123456", h) is True

    def test_verify_wrong_code(self):
        h = hash_code("123456")
        assert verify_code("654321", h) is False

    def test_hash_is_deterministic_same_key(self):
        # HMAC is keyed but deterministic: same key + same input → same output.
        # This is intentional — verify_code() compares with hmac.compare_digest.
        assert hash_code("123456") == hash_code("123456")

    def test_different_codes_produce_different_hashes(self):
        assert hash_code("111111") != hash_code("222222")