"""Blacklist HTTP schemas — admin denylist management."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.blacklist import BlacklistKind


class BlacklistAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    value: str = Field(..., min_length=1, max_length=255, description="Phone/email or IP to block")
    kind: BlacklistKind = Field(..., description="recipient or ip")
    reason: str | None = Field(default=None, max_length=255)


class BlacklistEntryResponse(BaseModel):
    id: uuid.UUID
    value: str
    kind: BlacklistKind
    reason: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)