"""
Message rendering — Jinja2-backed.

[LEARN]
Pattern: "Templates as data". The wording of an OTP message changes more
often than the code that sends it (legal review, locale, branding). Keep
copy in files (`app/templates/...`) so ops/marketing can change wording
without touching Python.

Why one shared `Environment`:
  Jinja2 compiles templates the first time they're rendered and caches the
  bytecode on the Environment. Re-creating an Environment per call discards
  that cache. One module-level instance amortises compilation across the
  process.

Auto-escaping policy:
  - `.html` / `.htm` → autoescape ON (will matter when HTML emails land)
  - `.txt` / `.subject` / `.j2` → autoescape OFF (plain text)
  `select_autoescape(["html", "htm"])` does the right thing.

Read more:
  - https://jinja.palletsprojects.com/en/stable/api/
  - https://jinja.palletsprojects.com/en/stable/api/#autoescaping
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.core.config import get_settings
from app.models.otp import OTPChannel

# `app/services/templates.py` → parent.parent = `app/` → / "templates"
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

# [LEARN] StrictUndefined: referencing a missing variable raises instead of
# rendering empty string — catches "{{ cdoe }}" typos in templates loudly.
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "htm"]),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass(slots=True)
class RenderedMessage:
    """Output of the render step — channel-agnostic, consumed by adapters."""

    body: str
    subject: str | None = None


def _ttl_minutes() -> int:
    """Round OTP TTL up to whole minutes for user-facing copy."""
    return max(1, math.ceil(get_settings().OTP_TTL_SECONDS / 60))


def render_otp(channel: OTPChannel, *, code: str) -> RenderedMessage:
    """
    Render the OTP message for the given channel.

    SMS: body only.
    Email: subject + body. (HTML body added in Month 3 when the dashboard ships.)
    """
    ctx = {"code": code, "ttl_minutes": _ttl_minutes()}

    if channel is OTPChannel.SMS:
        body = _env.get_template("sms/otp.txt.j2").render(**ctx).strip()
        return RenderedMessage(body=body)

    if channel is OTPChannel.EMAIL:
        body = _env.get_template("email/otp.txt.j2").render(**ctx).strip()
        subject = _env.get_template("email/otp.subject.j2").render(**ctx).strip()
        return RenderedMessage(body=body, subject=subject)

    raise ValueError(f"No template registered for channel {channel!r}")