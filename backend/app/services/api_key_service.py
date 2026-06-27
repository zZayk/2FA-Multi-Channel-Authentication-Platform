"""
APIKey service — issue, lookup, revoke.

[LEARN]
Pattern: "Deterministic-hash credential lookup".
The key's HMAC is its primary key from the auth path's POV: we hash the
incoming plaintext, query the row by `key_hash`. Because HMAC is keyed
with SECRET_KEY, two installations of this app *cannot* validate each
other's keys even with the same DB dump.

Why we never store the plaintext, even in logs:
  Once issued, plaintext exists only in the admin's hands. The service
  returns it exactly once at creation. Lose it → revoke + reissue. This
  matches the "secret material visible at issuance only" pattern used
  by GitHub PATs, Stripe keys, etc.

Read more:
  - OWASP "Authentication Cheat Sheet" §"Storing Credentials"
  - GitHub blog — "Behind GitHub's new authentication token formats"
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.api_key import APIKey

_KEY_PREFIX_LEN = 8         # "l2t_abc1" visible in audit logs
_DEFAULT_LAST_USED_DEBOUNCE = timedelta(seconds=60)


# =============================================================================
# Pure helpers
# =============================================================================

def generate_plaintext() -> str:
    """
    Issue a new plaintext key: `l2t_` prefix + 32 url-safe random bytes.

    The prefix lets developers spot a leaked key in commits / logs via grep,
    and lets us route legacy formats later if we change the scheme.
    """
    return f"l2t_{secrets.token_urlsafe(32)}"


def hash_key(plaintext: str) -> str:
    """HMAC-SHA256 hex digest, keyed with SECRET_KEY."""
    key = get_settings().SECRET_KEY.get_secret_value().encode()
    return hmac.new(key, plaintext.encode(), hashlib.sha256).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# =============================================================================
# CRUD
# =============================================================================

async def create_api_key(
    session: AsyncSession, *, name: str
) -> tuple[APIKey, str]:
    """
    Issue a new key. Returns (orm_row, plaintext).

    The plaintext is returned EXACTLY ONCE. The caller (admin UI / CLI)
    shows it to the user; persistent storage of the plaintext is forbidden.
    """
    plaintext = generate_plaintext()
    row = APIKey(
        name=name,
        key_hash=hash_key(plaintext),
        key_prefix=plaintext[:_KEY_PREFIX_LEN],
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row, plaintext


async def find_active_by_plaintext(
    session: AsyncSession, plaintext: str
) -> APIKey | None:
    """
    Look up an active key (not revoked) by its plaintext form.

    Returns None on miss — auth dependency turns that into 401.
    """
    stmt = (
        select(APIKey)
        .where(
            APIKey.key_hash == hash_key(plaintext),
            APIKey.revoked_at.is_(None),
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def touch_last_used(
    session: AsyncSession,
    key: APIKey,
    *,
    debounce: timedelta = _DEFAULT_LAST_USED_DEBOUNCE,
) -> None:
    """
    Update `last_used_at` at most once per `debounce` window.

    [LEARN] Without debouncing, every request hammers the same row →
    write amplification + lock contention on hot keys. Debouncing
    accepts staleness in exchange for cheap observability.
    """
    now = _utcnow()
    if key.last_used_at is not None and (now - key.last_used_at) < debounce:
        return
    key.last_used_at = now
    await session.commit()


async def revoke(session: AsyncSession, key_id: uuid.UUID) -> bool:
    """
    Mark a key revoked. Returns True if a row was updated, False if no-op.
    Already-revoked keys are left untouched (preserves original revoked_at).
    """
    key = await session.get(APIKey, key_id)
    if key is None or key.revoked_at is not None:
        return False
    key.revoked_at = _utcnow()
    await session.commit()
    return True