"""
OTP service — pure helper tests.

DB-backed tests (create_otp / verify_otp) are deferred to Week 3 once
testcontainers-postgres is wired up. Reason: model uses postgresql.UUID
which SQLite can't represent without a TypeDecorator hack.
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
        # Repeat enough times to surface low-bound codes that need padding.
        for _ in range(50):
            code = generate_code(n)
            assert len(code) == n
            assert code.isdigit()

    def test_codes_change_between_calls(self):
        # Crypto RNG: collisions on 6 digits are ~1/10^6.
        # 100 calls should produce >95 distinct values.
        codes = {generate_code(6) for _ in range(100)}
        assert len(codes) > 95


# =============================================================================
# hash_code / verify_code
# =============================================================================

class TestBcryptRoundtrip:
    def test_hash_is_not_plaintext(self):
        h = hash_code("123456")
        assert "123456" not in h
        assert h.startswith("$2b$")  # bcrypt prefix

    def test_verify_correct_code(self):
        h = hash_code("123456")
        assert verify_code("123456", h) is True

    def test_verify_wrong_code(self):
        h = hash_code("123456")
        assert verify_code("654321", h) is False

    def test_two_hashes_of_same_code_differ(self):
        # bcrypt embeds a per-hash salt → identical input ≠ identical output.
        assert hash_code("123456") != hash_code("123456")