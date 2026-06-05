"""
TunisiaSMS adapter — concrete `ChannelAdapter` for the SMS channel.

[LEARN]
Pattern: "Anti-Corruption Layer" (DDD).
External APIs change shape, return inconsistent error codes, or expose
quirks (e.g. TunisiaSMS returning HTTP 200 with `status: "rejected"` in
the body). The adapter translates those quirks into our clean domain
types (`SendResult`, `SendOutcome`). The rest of the codebase NEVER
sees a raw TunisiaSMS payload.

Read more:
  - Eric Evans, "Domain-Driven Design" — Anti-Corruption Layer
  - https://www.python-httpx.org/async/
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from app.core.config import get_settings
from app.models.otp import OTPChannel
from app.services.channels.base import (
    ChannelAdapter,
    PermanentChannelError,
    SendOutcome,
    SendResult,
    TransientChannelError,
    register_adapter,
)

logger = logging.getLogger(__name__)

# Connect ≤ 5s, read ≤ 10s — total cap protects the worker from a stuck call.
_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


class TunisiaSMSAdapter(ChannelAdapter):
    """
    Sends SMS via TunisiaSMS HTTP API.

    [LEARN] Why we build a fresh `AsyncClient` per call:
      `httpx.AsyncClient` binds to the asyncio loop that created it.
      Celery tasks call `asyncio.run(...)` which spins a new loop per task
      → a cached client would be tied to a dead loop and raise on second
      use. Trade-off: we lose HTTP keep-alive connection pooling. For
      OTP-rate traffic this is fine. If volume justifies it later, switch
      to a Celery worker pool that owns a persistent loop (e.g. `gevent`
      or a dedicated `asyncio` worker).
    """

    channel = OTPChannel.SMS

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.TUNISIASMS_API_URL.rstrip("/")
        self._api_key = settings.TUNISIASMS_API_KEY.get_secret_value()
        self._sender_id = settings.TUNISIASMS_SENDER_ID

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def send(
        self,
        *,
        recipient: str,
        body: str,
        correlation_id: uuid.UUID,
        subject: str | None = None,  # ignored — SMS has no subject
    ) -> SendResult:
        payload = {
            "to": recipient,
            "from": self._sender_id,
            "text": body,
            "client_ref": str(correlation_id),  # echoed back in DLR — links rows
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Correlation-ID": str(correlation_id),
        }

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._base_url}/messages", json=payload, headers=headers
                )
        except httpx.TimeoutException as e:
            logger.warning(
                "sms.timeout",
                extra={"correlation_id": str(correlation_id), "error": str(e)},
            )
            raise TransientChannelError(f"TunisiaSMS timeout: {e}") from e
        except httpx.HTTPError as e:
            # Network-layer error (DNS, conn reset). Retryable.
            logger.warning(
                "sms.network_error",
                extra={"correlation_id": str(correlation_id), "error": str(e)},
            )
            raise TransientChannelError(f"TunisiaSMS network error: {e}") from e

        return self._classify(resp, correlation_id=correlation_id)

    # -------------------------------------------------------------------------
    # Status mapping
    # -------------------------------------------------------------------------

    def _classify(
        self, resp: httpx.Response, *, correlation_id: uuid.UUID
    ) -> SendResult:
        """Map HTTP status + body into our domain `SendResult`."""
        # Parse JSON if available; never fatal if body is non-JSON.
        try:
            data: dict[str, Any] = resp.json() if resp.content else {}
        except ValueError:
            data = {}

        sc = resp.status_code
        provider_id = data.get("messageId") or data.get("message_id")

        # 2xx — accepted by provider (DLR may upgrade later)
        if 200 <= sc < 300:
            return SendResult(
                outcome=SendOutcome.ACCEPTED,
                provider_message_id=provider_id,
                raw=data,
            )

        # 429 — rate-limit (retry)
        if sc == 429:
            return SendResult(
                outcome=SendOutcome.RETRY,
                error_reason="rate_limited",
                raw=data,
            )

        # 5xx — provider-side failure (retry)
        if 500 <= sc < 600:
            return SendResult(
                outcome=SendOutcome.RETRY,
                error_reason=f"upstream_{sc}",
                raw=data,
            )

        # 401/403 — credential failure. Permanent until SRE rotates keys —
        # retrying just spams the provider. Raise to surface loud-fast.
        if sc in (401, 403):
            logger.error(
                "sms.auth_failure",
                extra={"correlation_id": str(correlation_id), "status": sc},
            )
            raise PermanentChannelError(
                f"TunisiaSMS auth failed ({sc}) — rotate TUNISIASMS_API_KEY"
            )

        # Any other 4xx — bad recipient, banned, malformed. Permanent.
        return SendResult(
            outcome=SendOutcome.REJECTED,
            error_reason=data.get("error") or f"client_{sc}",
            raw=data,
        )


# Module-level instance — registered on import so `get_adapter(OTPChannel.SMS)`
# works without an explicit wiring step.
sms_adapter = TunisiaSMSAdapter()
register_adapter(sms_adapter)