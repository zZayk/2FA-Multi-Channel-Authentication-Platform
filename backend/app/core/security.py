"""
Auth dependencies — API key verification.

[LEARN]
Pattern: "Authentication as a FastAPI dependency".
`require_api_key` is a coroutine injected via `Depends`. Any route that
lists it gains auth; routes that don't stay public. This is finer-grained
than middleware (which wraps EVERY request) and integrates with OpenAPI:
because we use `APIKeyHeader`, FastAPI emits a `securityScheme` in the
generated spec, so Swagger UI shows an "Authorize" button.

Why `auto_error=False`:
  With `auto_error=True`, FastAPI raises its own 403 when the header is
  missing — before our code runs. We want a consistent 401 + a clear
  message + `WWW-Authenticate`, so we disable the built-in error and
  handle the missing/invalid cases ourselves.

Read more:
  - https://fastapi.tiangolo.com/tutorial/security/
  - https://fastapi.tiangolo.com/reference/security/#fastapi.security.APIKeyHeader
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.models.api_key import APIKey
from app.services import api_key_service

# `name` is the HTTP header clients send. `auto_error=False` → we own the error.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Missing or invalid API key",
    headers={"WWW-Authenticate": "ApiKey"},
)


async def require_api_key(
    api_key: str | None = Security(api_key_header),
    session: AsyncSession = Depends(get_session),
) -> APIKey:
    """
    Resolve + validate the X-API-Key header.

    Raises 401 if the header is absent, malformed, unknown, or revoked.
    On success returns the APIKey row and refreshes `last_used_at`
    (debounced inside the service).
    """
    if not api_key:
        raise _UNAUTHENTICATED

    key_row = await api_key_service.find_active_by_plaintext(session, api_key)
    if key_row is None:
        raise _UNAUTHENTICATED

    await api_key_service.touch_last_used(session, key_row)
    return key_row