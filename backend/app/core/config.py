"""
Centralised settings — read once at import time, validated, typed.

[LEARN]
Pattern: "Typed configuration with Pydantic BaseSettings".
Every setting is declared with a type. At process start, pydantic loads values
from (in priority order):
    1. Constructor kwargs (used in tests: `Settings(OTP_TTL_SECONDS=10)`)
    2. Environment variables
    3. The `.env` file referenced by model_config
    4. Default values in the class

If a required value is missing or the type is wrong, instantiation raises
`ValidationError` — the app refuses to start. This is intentional: it's
better to crash on boot than to limp along with a None password.

Read more:
  - https://docs.pydantic.dev/latest/concepts/pydantic_settings/
  - 12-factor app, §III "Config" (https://12factor.net/config)
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Populated from environment + .env file."""

    # -------------------------------------------------------------------------
    # Meta
    # -------------------------------------------------------------------------
    ENVIRONMENT: Literal["dev", "staging", "prod"] = "dev"
    APP_NAME: str = "2FA Multi-Channel Authentication Platform"
    APP_VERSION: str = "0.1.0"

    # -------------------------------------------------------------------------
    # Security
    # -------------------------------------------------------------------------
    # [LEARN] SecretStr prevents accidental leakage via str() / repr() / logs.
    # `print(settings.SECRET_KEY)` shows "**********", not the value.
    # To use the real value: `settings.SECRET_KEY.get_secret_value()`.
    SECRET_KEY: SecretStr = Field(..., description="App-wide signing/HMAC key")

    # -------------------------------------------------------------------------
    # Database (async SQLAlchemy + asyncpg)
    # -------------------------------------------------------------------------
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://app:app@db:5432/app",
        description="SQLAlchemy async URL — must use the +asyncpg driver",
    )

    # -------------------------------------------------------------------------
    # Redis (cache + Celery broker + rate limit)
    # -------------------------------------------------------------------------
    REDIS_URL: str = Field(default="redis://redis:6379/0")
    CELERY_BROKER_URL: str = Field(default="redis://redis:6379/1")
    CELERY_RESULT_BACKEND: str = Field(default="redis://redis:6379/2")

    # -------------------------------------------------------------------------
    # OTP lifecycle
    # -------------------------------------------------------------------------
    OTP_TTL_SECONDS: int = Field(default=300, ge=30, le=900)
    MAX_OTP_ATTEMPTS: int = Field(default=5, ge=1, le=10)
    OTP_CODE_LENGTH: int = Field(default=6, ge=4, le=10)

    # -------------------------------------------------------------------------
    # TunisiaSMS channel adapter
    # -------------------------------------------------------------------------
    TUNISIASMS_API_URL: str = Field(default="https://api.tunisiasms.tn/v1")
    TUNISIASMS_API_KEY: SecretStr = Field(default=SecretStr(""))
    TUNISIASMS_SENDER_ID: str = Field(default="L2T-2FA")

    # -------------------------------------------------------------------------
    # DLR (delivery receipt) polling — Month 2, Slice 1
    # -------------------------------------------------------------------------
    # How often Beat fires the poll task (seconds).
    DLR_POLL_INTERVAL_SECONDS: int = Field(default=30, ge=5, le=600)
    # Max rows reconciled per poll tick — bounds DB + provider load per run.
    DLR_POLL_BATCH_SIZE: int = Field(default=100, ge=1, le=1000)
    # A row still SENT (no terminal DLR) past this age → declared FAILED so it
    # never wedges forever. Should exceed realistic carrier DLR latency. Rows
    # older than this are failed *without* a (pointless) DLR call.
    DLR_TIMEOUT_SECONDS: int = Field(default=600, ge=60)

    # -------------------------------------------------------------------------
    # Email channel adapter (SMTP)
    # -------------------------------------------------------------------------
    EMAIL_HOST: str = Field(default="localhost")
    EMAIL_PORT: int = Field(default=1025)  # MailHog default in dev
    EMAIL_USER: str = Field(default="")
    EMAIL_PASSWORD: SecretStr = Field(default=SecretStr(""))
    EMAIL_FROM: str = Field(default="noreply@l2t.tn")
    EMAIL_USE_TLS: bool = Field(default=False)

    # -------------------------------------------------------------------------
    # Anti-abuse engine
    # -------------------------------------------------------------------------
    ABUSE_RULE_MAX_PER_HOUR: int = Field(default=10, ge=1)
    ABUSE_RULE_MAX_PER_DAY: int = Field(default=50, ge=1)
    # Per-IP hourly cap — higher than per-recipient since one IP (NAT/proxy)
    # can legitimately serve many users.
    ABUSE_RULE_IP_MAX_PER_HOUR: int = Field(default=30, ge=1)
    # Isolation Forest decision threshold — score above this = block.
    # Inert while ml.anomaly_score is a stub (returns 0.0).
    ABUSE_ML_SCORE_THRESHOLD: float = Field(default=0.75, ge=0.0, le=1.0)

    # -------------------------------------------------------------------------
    # pydantic-settings loader config
    # -------------------------------------------------------------------------
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",  # ignore unknown env vars (Docker injects many)
    )


# [LEARN] Why @lru_cache on the getter?
# Settings() reads the .env file from disk + walks all env vars. Doing that on
# every Depends() call would be wasteful. @lru_cache memoises the instance for
# the process lifetime — same effect as a module-level singleton, but lets tests
# do `get_settings.cache_clear()` to force a reload between cases.
# Pattern: "Singleton via memoised factory".
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings singleton."""
    return Settings()  # type: ignore[call-arg]  # pydantic-settings reads from env
