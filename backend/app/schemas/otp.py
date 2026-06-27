"""
OTP HTTP schemas — Pydantic v2.

[LEARN]
Pattern: "Schema / model separation".
  schemas/ → wire format (request / response)
  models/  → DB rows
Never `response_model=OTP` (the ORM model). That leaks columns the client
should never see (`code_hash`, internal status, attempt_count if not intended).
Define an explicit response schema that lists only what's safe to send.

Read more:
  - https://fastapi.tiangolo.com/tutorial/response-model/
  - OWASP — "Mass Assignment Cheat Sheet"
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.otp import OTPChannel, OTPStatus

# --- Validators ---------------------------------------------------------------

# E.164: optional leading '+', then 8–15 digits. Country code + national number.
# Spec: https://en.wikipedia.org/wiki/E.164
_E164_RE = re.compile(r"^\+?[1-9]\d{7,14}$")


def _validate_recipient(channel: OTPChannel, recipient: str) -> str:
    """
    Recipient must match the channel:
      SMS   → E.164 phone
      EMAIL → RFC 5322 email (delegated to pydantic EmailStr by the caller)
    """
    recipient = recipient.strip()
    if channel is OTPChannel.SMS:
        if not _E164_RE.match(recipient):
            raise ValueError("recipient must be an E.164 phone for SMS channel")
    elif channel is OTPChannel.EMAIL:
        # Lightweight check; EmailStr-grade validation is overkill here because
        # the canonical email lookup column will be normalized downstream.
        if "@" not in recipient or " " in recipient:
            raise ValueError("recipient must be an email address for EMAIL channel")
    return recipient


# =============================================================================
# Requests
# =============================================================================

class OTPRequest(BaseModel):
    """POST /otp/request — ask the service to create + send an OTP."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    channel: OTPChannel = Field(..., description="Delivery channel")
    recipient: str = Field(
        ...,
        min_length=3,
        max_length=255,
        description="E.164 phone (SMS) or email address (EMAIL)",
        examples=["+21620000000", "user@example.com"],
    )
    correlation_id: uuid.UUID | None = Field(
        default=None,
        description="Optional client-supplied trace ID; generated if omitted",
    )

    @field_validator("recipient")
    @classmethod
    def _check_recipient(cls, v: str, info) -> str:
        channel = info.data.get("channel")
        if channel is None:
            return v  # let channel validation surface its own error first
        return _validate_recipient(channel, v)


class OTPVerifyRequest(BaseModel):
    """POST /otp/verify — submit a code to verify."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    recipient: str = Field(..., min_length=3, max_length=255)
    code: str = Field(
        ...,
        min_length=4,
        max_length=10,
        pattern=r"^\d+$",
        description="Numeric OTP, length per OTP_CODE_LENGTH setting",
    )


class OTPResendRequest(BaseModel):
    """
    POST /otp/resend — re-issue an OTP on a (usually different) channel.

    This is the *user-initiated* fallback: when an SMS shows FAILED, the client
    offers "try another method" and calls this with channel=email + the user's
    email. Pass the original `correlation_id` to keep the whole auth attempt
    traceable as one chain across channels.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    channel: OTPChannel = Field(..., description="Channel to resend on")
    recipient: str = Field(..., min_length=3, max_length=255)
    correlation_id: uuid.UUID | None = Field(
        default=None,
        description="Original attempt's correlation_id — links the resend to it",
    )

    @field_validator("recipient")
    @classmethod
    def _check_recipient(cls, v: str, info) -> str:
        channel = info.data.get("channel")
        if channel is None:
            return v
        return _validate_recipient(channel, v)


# =============================================================================
# Responses
# =============================================================================

class OTPCreatedResponse(BaseModel):
    """Returned on successful OTP creation. The plaintext code is NEVER returned."""

    id: uuid.UUID
    channel: OTPChannel
    expires_at: datetime
    correlation_id: uuid.UUID
    status: OTPStatus

    model_config = ConfigDict(from_attributes=True)  # build from ORM instance


class OTPVerifyResponse(BaseModel):
    """Returned by /otp/verify — minimal, no PII echoed back."""

    verified: bool
    attempts_remaining: int = Field(..., ge=0)
    status: OTPStatus


class OTPStatusResponse(BaseModel):
    """
    GET /otp/{id} — delivery status of one OTP.

    The client polls this after /otp/request to learn whether the SMS landed
    (DELIVERED) or failed (FAILED). On FAILED it may offer the user a resend.
    Never exposes `code_hash` or the plaintext.
    """

    id: uuid.UUID
    channel: OTPChannel
    recipient: str
    status: OTPStatus
    sent_at: datetime | None = None
    delivered_at: datetime | None = None
    failed_at: datetime | None = None
    correlation_id: uuid.UUID
    #: Convenience flag for the client UI — true when a fallback resend makes sense.
    fallback_available: bool = False

    model_config = ConfigDict(from_attributes=True)


class ChannelDeliveryMetrics(BaseModel):
    """Per-channel delivery aggregates for the dashboard."""

    channel: OTPChannel
    total: int
    delivered: int
    failed: int
    in_flight: int
    delivery_rate: float = Field(..., ge=0.0, le=1.0)
    avg_delivery_seconds: float | None = None


class DeliveryMetricsResponse(BaseModel):
    """GET /otp/metrics — SMS-vs-Email delivery performance."""

    channels: list[ChannelDeliveryMetrics]


# =============================================================================
# Email-only convenience
# =============================================================================
# Exposed if a downstream consumer prefers strict email validation. The
# main OTPRequest accepts a plain str for symmetry with the SMS path.
class EmailOTPRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient: EmailStr
    correlation_id: uuid.UUID | None = None