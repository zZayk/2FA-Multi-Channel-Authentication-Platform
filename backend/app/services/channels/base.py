"""
Channel adapter base — abstract contract every concrete channel implements.

[LEARN]
Pattern: "Strategy" (GoF) / "Port-Adapter" (Hexagonal Architecture).
The dispatch layer talks to `ChannelAdapter`; concrete classes
(`TunisiaSMSAdapter`, `SmtpEmailAdapter`) are swap-in implementations.

Why split errors into Transient vs Permanent:
  Celery's retry policy keys off exception type. Transient (network blip,
  5xx, rate-limit) → retry with backoff. Permanent (invalid recipient,
  4xx-not-rate-limit, malformed body) → give up immediately, surface to
  the user. Mixing them = retry storms on bad data.

Read more:
  - GoF "Strategy"
  - Alistair Cockburn — "Hexagonal Architecture"
  - PEP 544 (Protocols, alt to ABC for structural typing)
"""

from __future__ import annotations

import enum
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.models.otp import OTPChannel


# =============================================================================
# Outcome enum
# =============================================================================

class SendOutcome(str, enum.Enum):
    """
    Coarse-grained result of a single send attempt.

      ACCEPTED  — provider acknowledged receipt (HTTP 2xx / SMTP 250).
                  Final state for the synchronous step; DLR may upgrade to
                  DELIVERED later (SMS) or downgrade to FAILED.
      REJECTED  — provider refused (bad recipient, blacklisted, malformed).
                  Permanent — do NOT retry.
      RETRY     — transient error (timeout, 5xx, network). Caller should
                  re-enqueue with backoff.
    """

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    RETRY = "retry"


# =============================================================================
# Value object
# =============================================================================

@dataclass(slots=True)
class SendResult:
    """Returned by every `ChannelAdapter.send(...)` call."""

    outcome: SendOutcome
    provider_message_id: str | None = None
    error_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# DLR (Delivery Receipt) value objects
# =============================================================================
# [LEARN] Why a *separate* result type from SendResult?
# `send()` is synchronous-ish: "did the provider accept it?" → ACCEPTED.
# DLR answers a *later, different* question: "did it actually reach the
# handset?" SMS delivery is asynchronous — the provider returns 202 in
# milliseconds, then a carrier confirmation (or failure) arrives seconds-to-
# minutes later. Conflating the two states (acceptance vs delivery) is a
# classic SMS-integration bug. Distinct types keep the state machine honest.
# Read more: GSM 03.40 SMS-STATUS-REPORT (the DLR concept at protocol level).

class DlrStatus(str, enum.Enum):
    """
    Terminal-vs-pending classification of a delivery receipt.

      DELIVERED — carrier confirmed handset receipt. Terminal (good).
      FAILED    — carrier rejected / undeliverable. Terminal (bad) → fallback.
      PENDING   — provider still has it in flight; poll again later.
      UNKNOWN   — provider has no record of this id (lost / bad id). Treated
                  as non-terminal but logged loudly; the timeout sweep will
                  eventually FAIL it so it never wedges forever.
    """

    DELIVERED = "delivered"
    FAILED = "failed"
    PENDING = "pending"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class DlrResult:
    """Returned by `ChannelAdapter.query_dlr(...)`."""

    status: DlrStatus
    error_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Error hierarchy
# =============================================================================

class ChannelError(Exception):
    """Root for all channel-adapter errors."""


class TransientChannelError(ChannelError):
    """Retryable — network timeout, 5xx, provider rate-limit."""


class PermanentChannelError(ChannelError):
    """Do not retry — bad recipient, auth failure, malformed body."""


# =============================================================================
# Adapter ABC
# =============================================================================

class ChannelAdapter(ABC):
    """
    Contract for a single delivery channel.

    Implementations live in `app/services/channels/{sms,email,...}.py`.
    Concrete adapters MUST be safe to instantiate once and reuse — they
    should hold their own httpx.AsyncClient / aiosmtplib pool internally.
    """

    #: Maps the adapter to its OTPChannel enum value. Subclasses set this.
    channel: OTPChannel

    @abstractmethod
    async def send(
        self,
        *,
        recipient: str,
        body: str,
        correlation_id: uuid.UUID,
        subject: str | None = None,
    ) -> SendResult:
        """
        Deliver `body` to `recipient`.

        MUST:
          - return a `SendResult` for normal outcomes (ACCEPTED, REJECTED, RETRY)
          - raise `TransientChannelError` or `PermanentChannelError` if the
            failure should propagate to Celery retry logic instead of being
            returned as a result
          - never log or store the body in plaintext beyond the call scope
        """
        raise NotImplementedError

    async def query_dlr(
        self,
        *,
        provider_message_id: str,
        correlation_id: uuid.UUID,
    ) -> DlrResult:
        """
        Ask the provider for the delivery status of a previously-sent message.

        Default raises — not every channel has async delivery receipts.
        Email (SMTP) is terminal at send time (250 = queued/accepted by the
        next hop), so the email adapter intentionally does NOT override this.
        The DLR poll task only ever queries channels that do (SMS).
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support DLR polling"
        )

    async def aclose(self) -> None:
        """Cleanup hook — subclasses override if they hold open connections."""
        return None


# =============================================================================
# Registry — populated in Substeps C & D, queried by dispatch task
# =============================================================================

_REGISTRY: dict[OTPChannel, ChannelAdapter] = {}


def register_adapter(adapter: ChannelAdapter) -> None:
    """Register an adapter against its `channel` attribute. Idempotent."""
    _REGISTRY[adapter.channel] = adapter


def get_adapter(channel: OTPChannel) -> ChannelAdapter:
    """Resolve channel → adapter. Raises KeyError if not registered."""
    try:
        return _REGISTRY[channel]
    except KeyError as e:
        raise LookupError(
            f"No adapter registered for channel {channel.value!r}. "
            f"Did you forget to import app.services.channels.sms / .email?"
        ) from e