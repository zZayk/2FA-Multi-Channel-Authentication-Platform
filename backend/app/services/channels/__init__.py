"""
Channel adapters — strategy pattern.

Every delivery channel (SMS, Email, future WhatsApp/Push) implements the
same `ChannelAdapter` interface. The dispatch task picks an adapter by
`OTPChannel` enum and calls `.send(...)` — it never knows which provider
is behind the curtain.

Public surface:
  - ChannelAdapter      : abstract base
  - SendResult          : value object returned by send()
  - SendOutcome         : ACCEPTED / REJECTED / RETRY enum
  - ChannelError        : root of channel error hierarchy
  - TransientChannelError / PermanentChannelError
  - get_adapter(channel): resolves OTPChannel → adapter instance (wired in C/D)
"""

from app.services.channels.base import (
    ChannelAdapter,
    ChannelError,
    PermanentChannelError,
    SendOutcome,
    SendResult,
    TransientChannelError,
)

__all__ = [
    "ChannelAdapter",
    "ChannelError",
    "PermanentChannelError",
    "SendOutcome",
    "SendResult",
    "TransientChannelError",
]