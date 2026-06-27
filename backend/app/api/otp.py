"""
OTP HTTP boundary — thin router.

[LEARN]
Router responsibilities (and ONLY these):
  1. Parse the request (Pydantic does this for free via type annotations).
  2. Call a service function.
  3. Shape the response.

No SQL, no hashing, no validation that belongs in schemas. If you find
yourself writing an `if otp.expires_at < now: ...` here, push it down to
`services/otp_service.py`.

Read more:
  - https://fastapi.tiangolo.com/tutorial/bigger-applications/
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import require_api_key
from app.models.api_key import APIKey
from app.models.otp import OTPChannel, OTPStatus
from app.schemas.otp import (
    ChannelDeliveryMetrics,
    DeliveryMetricsResponse,
    OTPCreatedResponse,
    OTPRequest,
    OTPResendRequest,
    OTPStatusResponse,
    OTPVerifyRequest,
    OTPVerifyResponse,
)
from app.services import abuse, otp_service
from app.tasks.dispatch import dispatch_otp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/otp", tags=["otp"])

# Shared response docs for Swagger — both routes are auth-gated.
_AUTH_RESPONSES: dict = {
    401: {"description": "Missing or invalid API key"},
}


def _client_ip(request: Request) -> str | None:
    """
    Best-effort client IP.

    [LEARN] Behind a reverse proxy, `request.client.host` is the proxy, not the
    user. We honour `X-Forwarded-For` (first hop = original client) when present.
    SECURITY: XFF is client-spoofable unless the proxy overwrites it — trust it
    only behind a proxy you control. v1 reads it; Month-4 hardening pins the
    trusted-proxy set.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None


async def _gate_or_block(
    session: AsyncSession,
    *,
    channel: OTPChannel,
    recipient: str,
    ip: str | None,
    correlation_id: uuid.UUID | None,
    api_key_id: uuid.UUID,
) -> None:
    """
    Run the abuse engine. On block: record a BLOCKED audit row and raise 429.

    Shared by /request and /resend so both entry points are gated identically.
    """
    decision = await abuse.evaluate(session, recipient=recipient, ip=ip)
    if decision.allowed:
        return

    await otp_service.record_blocked(
        session,
        channel=channel,
        recipient=recipient,
        ip_address=ip,
        correlation_id=correlation_id,
    )
    logger.warning(
        "otp.blocked",
        extra={
            "event": "otp_blocked",
            "recipient": recipient,
            "ip": ip,
            "rule": decision.rule,
            "reason": decision.reason,
            "api_key_id": str(api_key_id),
        },
    )
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"blocked": True, "rule": decision.rule, "reason": decision.reason},
    )


@router.post(
    "/request",
    response_model=OTPCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an OTP and dispatch it on the requested channel",
    responses={
        **_AUTH_RESPONSES,
        201: {"description": "OTP created and queued for delivery"},
        422: {"description": "Validation error (bad channel/recipient shape)"},
    },
)
async def request_otp(
    payload: OTPRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> OTPCreatedResponse:
    """
    Create an OTP, persist it, and enqueue dispatch on the chosen channel.

    Requires a valid `X-API-Key`. Runs the anti-abuse engine first (blacklist +
    rate rules); a blocked request returns 429. The plaintext code is NEVER
    returned in the HTTP response — it leaves the process only via the channel.
    """
    ip = _client_ip(request)
    await _gate_or_block(
        session,
        channel=payload.channel,
        recipient=payload.recipient,
        ip=ip,
        correlation_id=payload.correlation_id,
        api_key_id=api_key.id,
    )

    otp, plaintext_code = await otp_service.create_otp(
        session,
        channel=payload.channel,
        recipient=payload.recipient,
        correlation_id=payload.correlation_id,
        ip_address=ip,
    )

    # Enqueue the channel send. Plaintext leaves the API process here →
    # crosses Redis broker → consumed by worker. See decisions.md for the
    # plaintext-in-broker trade-off accepted at v1.
    dispatch_otp.delay(str(otp.id), plaintext_code)

    logger.info(
        "otp.created",
        extra={
            "event": "otp_created",
            "otp_id": str(otp.id),
            "channel": otp.channel.value,
            "correlation_id": str(otp.correlation_id),
            "api_key_id": str(api_key.id),  # which key issued this request
        },
    )

    return OTPCreatedResponse.model_validate(otp)


@router.post(
    "/resend",
    response_model=OTPCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="User-initiated fallback: re-issue an OTP on another channel",
    responses={
        **_AUTH_RESPONSES,
        201: {"description": "OTP re-issued and queued on the requested channel"},
        422: {"description": "Validation error (bad channel/recipient shape)"},
    },
)
async def resend_otp(
    payload: OTPResendRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> OTPCreatedResponse:
    """
    Re-issue an OTP on a (usually different) channel — the user-initiated
    fallback after an SMS shows FAILED. A fresh code is generated; pass the
    original `correlation_id` to keep the attempt chain traceable. Gated by the
    same anti-abuse engine as /request.
    """
    ip = _client_ip(request)
    await _gate_or_block(
        session,
        channel=payload.channel,
        recipient=payload.recipient,
        ip=ip,
        correlation_id=payload.correlation_id,
        api_key_id=api_key.id,
    )

    otp, plaintext_code = await otp_service.create_otp(
        session,
        channel=payload.channel,
        recipient=payload.recipient,
        correlation_id=payload.correlation_id,
        ip_address=ip,
    )
    dispatch_otp.delay(str(otp.id), plaintext_code)

    logger.info(
        "otp.resend",
        extra={
            "event": "otp_resend",
            "otp_id": str(otp.id),
            "channel": otp.channel.value,
            "correlation_id": str(otp.correlation_id),
            "api_key_id": str(api_key.id),
        },
    )
    return OTPCreatedResponse.model_validate(otp)


@router.get(
    "/metrics",
    response_model=DeliveryMetricsResponse,
    summary="Per-channel delivery metrics (SMS vs Email)",
    responses=_AUTH_RESPONSES,
)
async def otp_metrics(
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> DeliveryMetricsResponse:
    """Delivery-rate + latency aggregates per channel, for the dashboard."""
    rows = await otp_service.delivery_metrics(session)
    return DeliveryMetricsResponse(
        channels=[ChannelDeliveryMetrics.model_validate(m, from_attributes=True) for m in rows]
    )


@router.get(
    "/{otp_id}",
    response_model=OTPStatusResponse,
    summary="Delivery status of one OTP (poll after /request to detect FAILED)",
    responses={
        **_AUTH_RESPONSES,
        404: {"description": "No OTP with that id"},
    },
)
async def otp_status(
    otp_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> OTPStatusResponse:
    """
    Return one OTP's delivery status. The client polls this; on `FAILED` it
    may offer the user a resend on another channel (sets `fallback_available`).
    """
    otp = await otp_service.get_otp(session, otp_id)
    if otp is None:
        raise HTTPException(status_code=404, detail="OTP not found")

    resp = OTPStatusResponse.model_validate(otp)
    # Offer a fallback when an SMS terminally failed.
    resp.fallback_available = (
        otp.status is OTPStatus.FAILED and otp.failed_at is not None
    )
    return resp


@router.post(
    "/verify",
    response_model=OTPVerifyResponse,
    summary="Verify a submitted OTP code against the latest active OTP for the recipient",
    responses={
        **_AUTH_RESPONSES,
        200: {"description": "Verification result (verified true/false)"},
        422: {"description": "Validation error (bad recipient/code shape)"},
    },
)
async def verify_otp(
    payload: OTPVerifyRequest,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> OTPVerifyResponse:
    outcome = await otp_service.verify_otp(
        session,
        recipient=payload.recipient,
        code=payload.code,
    )
    return OTPVerifyResponse(
        verified=outcome.verified,
        attempts_remaining=outcome.attempts_remaining,
        status=outcome.status,
    )