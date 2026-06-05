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

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import require_api_key
from app.models.api_key import APIKey
from app.schemas.otp import (
    OTPCreatedResponse,
    OTPRequest,
    OTPVerifyRequest,
    OTPVerifyResponse,
)
from app.services import otp_service
from app.tasks.dispatch import dispatch_otp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/otp", tags=["otp"])

# Shared response docs for Swagger — both routes are auth-gated.
_AUTH_RESPONSES: dict = {
    401: {"description": "Missing or invalid API key"},
}


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
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> OTPCreatedResponse:
    """
    Create an OTP, persist it, and enqueue dispatch on the chosen channel.

    Requires a valid `X-API-Key`. The plaintext code is NEVER returned in
    the HTTP response — it leaves the process only via the delivery channel.
    """
    otp, plaintext_code = await otp_service.create_otp(
        session,
        channel=payload.channel,
        recipient=payload.recipient,
        correlation_id=payload.correlation_id,
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