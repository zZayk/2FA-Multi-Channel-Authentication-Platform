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
from app.schemas.otp import (
    OTPCreatedResponse,
    OTPRequest,
    OTPVerifyRequest,
    OTPVerifyResponse,
)
from app.services import otp_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/otp", tags=["otp"])


@router.post(
    "/request",
    response_model=OTPCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an OTP and dispatch it on the requested channel",
)
async def request_otp(
    payload: OTPRequest,
    session: AsyncSession = Depends(get_session),
) -> OTPCreatedResponse:
    """
    Week 2: persists the OTP and returns metadata. The actual SMS/Email
    dispatch lands in Week 3 when channel adapters are wired in.

    The plaintext code is NEVER returned in the HTTP response — it leaves
    the process only via the chosen delivery channel.
    """
    otp, _plaintext_code = await otp_service.create_otp(
        session,
        channel=payload.channel,
        recipient=payload.recipient,
        correlation_id=payload.correlation_id,
    )

    # Week 3 hook:
    #   from app.tasks.channels import dispatch_otp
    #   dispatch_otp.delay(otp.id, _plaintext_code)
    # For now we log the correlation_id so dev can trace the row.
    logger.info(
        "otp.created",
        extra={
            "event": "otp_created",
            "otp_id": str(otp.id),
            "channel": otp.channel.value,
            "correlation_id": str(otp.correlation_id),
        },
    )

    return OTPCreatedResponse.model_validate(otp)


@router.post(
    "/verify",
    response_model=OTPVerifyResponse,
    summary="Verify a submitted OTP code against the latest active OTP for the recipient",
)
async def verify_otp(
    payload: OTPVerifyRequest,
    session: AsyncSession = Depends(get_session),
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