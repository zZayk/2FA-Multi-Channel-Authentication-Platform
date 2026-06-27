"""
Admin HTTP boundary — denylist management for the anti-abuse engine.

Thin router (parse → service → response), same contract as the OTP router.
All routes are auth-gated by `X-API-Key`. In Month 4 these get a stronger
admin-scoped key / role; for now any valid key may manage the blacklist.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import require_api_key
from app.models.api_key import APIKey
from app.schemas.blacklist import BlacklistAddRequest, BlacklistEntryResponse
from app.services import abuse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

_AUTH_RESPONSES: dict = {401: {"description": "Missing or invalid API key"}}


@router.post(
    "/blacklist",
    response_model=BlacklistEntryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a recipient or IP to the abuse denylist",
    responses=_AUTH_RESPONSES,
)
async def add_blacklist(
    payload: BlacklistAddRequest,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> BlacklistEntryResponse:
    entry = await abuse.add_to_blacklist(
        session, value=payload.value, kind=payload.kind, reason=payload.reason
    )
    logger.info(
        "admin.blacklist_add",
        extra={"value": entry.value, "kind": entry.kind.value, "api_key_id": str(api_key.id)},
    )
    return BlacklistEntryResponse.model_validate(entry)


@router.get(
    "/blacklist",
    response_model=list[BlacklistEntryResponse],
    summary="List all denylist entries",
    responses=_AUTH_RESPONSES,
)
async def list_blacklist(
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> list[BlacklistEntryResponse]:
    entries = await abuse.list_blacklist(session)
    return [BlacklistEntryResponse.model_validate(e) for e in entries]


@router.delete(
    "/blacklist/{value}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a value from the denylist",
    responses={**_AUTH_RESPONSES, 404: {"description": "Value not on the denylist"}},
)
async def remove_blacklist(
    value: str,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> Response:
    removed = await abuse.remove_from_blacklist(session, value=value)
    if not removed:
        raise HTTPException(status_code=404, detail="value not on denylist")
    return Response(status_code=status.HTTP_204_NO_CONTENT)