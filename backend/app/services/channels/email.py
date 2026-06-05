"""
SMTP email adapter — concrete `ChannelAdapter` for the EMAIL channel.

[LEARN]
Pattern: same Strategy / Anti-Corruption Layer as the SMS adapter.

aiosmtplib gives us an async SMTP client — fits FastAPI's async stack and
avoids `loop.run_in_executor` for stdlib `smtplib`. Connection lifecycle
is short-lived (open, send one, quit), matching the SMS adapter's
per-call client decision for the same reason: Celery + `asyncio.run`
spins a fresh loop each task.

SMTP error taxonomy:
  • 4xx response  → transient (e.g. greylisting, temp failure) → retry
  • 5xx response  → permanent (bad recipient, message rejected) → REJECTED
  • Auth failure  → permanent (ops rotate creds) → PermanentChannelError
  • Connect/timeout → transient → TransientChannelError

Read more:
  - https://aiosmtplib.readthedocs.io/
  - RFC 5321 §4.2.1 (SMTP reply codes)
"""

from __future__ import annotations

import email.utils
import logging
import uuid
from email.message import EmailMessage

import aiosmtplib

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

_SMTP_TIMEOUT = 10.0  # seconds — covers connect + handshake + send
_DEFAULT_SUBJECT = "Your verification code"


class SmtpEmailAdapter(ChannelAdapter):
    """
    SMTP via aiosmtplib. Builds + sends one message per call.

    The `EMAIL_USE_TLS` setting picks the TLS mode:
      • True  → implicit TLS (port 465-style wrapped)
      • False → STARTTLS upgrade if the server advertises it (port 587)
    For dev with MailHog (port 1025), keep both off.
    """

    channel = OTPChannel.EMAIL

    def __init__(self) -> None:
        settings = get_settings()
        self._host = settings.EMAIL_HOST
        self._port = settings.EMAIL_PORT
        self._user = settings.EMAIL_USER
        self._password = settings.EMAIL_PASSWORD.get_secret_value()
        self._from_addr = settings.EMAIL_FROM
        self._use_tls = settings.EMAIL_USE_TLS

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def send(
        self,
        *,
        recipient: str,
        body: str,
        correlation_id: uuid.UUID,
        subject: str | None = None,
    ) -> SendResult:
        msg = self._build_message(
            recipient=recipient,
            body=body,
            correlation_id=correlation_id,
            subject=subject or _DEFAULT_SUBJECT,
        )
        # We own the Message-ID — also use it as our provider_message_id
        # so we can correlate SMTP server logs back to our OTP rows.
        message_id = msg["Message-ID"]

        try:
            await aiosmtplib.send(
                msg,
                hostname=self._host,
                port=self._port,
                username=self._user or None,
                password=self._password or None,
                use_tls=self._use_tls,
                start_tls=False if self._use_tls else None,  # auto-detect STARTTLS
                timeout=_SMTP_TIMEOUT,
            )
        except aiosmtplib.SMTPAuthenticationError as e:
            logger.error(
                "email.auth_failure",
                extra={"correlation_id": str(correlation_id), "error": str(e)},
            )
            raise PermanentChannelError(
                f"SMTP auth failed — rotate EMAIL_USER/EMAIL_PASSWORD: {e}"
            ) from e
        except aiosmtplib.SMTPRecipientsRefused as e:
            # Server rejected every recipient — permanent bad address.
            return SendResult(
                outcome=SendOutcome.REJECTED,
                provider_message_id=message_id,
                error_reason=f"recipients_refused: {e}",
            )
        except aiosmtplib.SMTPResponseException as e:
            # 4xx = transient, 5xx = permanent (RFC 5321 §4.2.1).
            if 400 <= e.code < 500:
                raise TransientChannelError(
                    f"SMTP {e.code} (transient): {e.message}"
                ) from e
            return SendResult(
                outcome=SendOutcome.REJECTED,
                provider_message_id=message_id,
                error_reason=f"smtp_{e.code}: {e.message}",
            )
        except (aiosmtplib.SMTPConnectError, aiosmtplib.SMTPServerDisconnected) as e:
            logger.warning(
                "email.connect_error",
                extra={"correlation_id": str(correlation_id), "error": str(e)},
            )
            raise TransientChannelError(f"SMTP connect: {e}") from e
        except (aiosmtplib.SMTPTimeoutError, TimeoutError) as e:
            logger.warning(
                "email.timeout",
                extra={"correlation_id": str(correlation_id), "error": str(e)},
            )
            raise TransientChannelError(f"SMTP timeout: {e}") from e

        return SendResult(
            outcome=SendOutcome.ACCEPTED,
            provider_message_id=message_id,
        )

    # -------------------------------------------------------------------------
    # Message construction
    # -------------------------------------------------------------------------

    def _build_message(
        self,
        *,
        recipient: str,
        body: str,
        correlation_id: uuid.UUID,
        subject: str,
    ) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self._from_addr
        msg["To"] = recipient
        msg["Subject"] = subject
        # Stable, ourselves-generated Message-ID — server may rewrite, but
        # most relays preserve it; we use it as our provider_message_id.
        msg["Message-ID"] = email.utils.make_msgid(domain="l2t.tn")
        # Custom header carrying our trace ID — visible in SMTP server logs.
        msg["X-Correlation-ID"] = str(correlation_id)
        msg.set_content(body)
        return msg


# Module-level singleton — registered on import.
email_adapter = SmtpEmailAdapter()
register_adapter(email_adapter)