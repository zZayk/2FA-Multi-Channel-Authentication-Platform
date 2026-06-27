"""
Anti-abuse engine package.

Public surface:
  evaluate(session, *, recipient, ip)  -> AbuseDecision   (the send-path gate)
  add_to_blacklist / remove_from_blacklist / list_blacklist
  AbuseDecision

The OTP send path calls `evaluate` BEFORE creating an OTP. Layered checks,
cheapest + most certain first:
  1. blacklist  — hard denylist, O(1) indexed lookup, short-circuit
  2. rate rules — per-recipient + per-IP volume caps (explainable)
  3. ML         — Isolation Forest anomaly score (scaffolded; see ml.py)
"""

from __future__ import annotations

from app.services.abuse.engine import (
    AbuseDecision,
    add_to_blacklist,
    evaluate,
    list_blacklist,
    remove_from_blacklist,
)

__all__ = [
    "AbuseDecision",
    "evaluate",
    "add_to_blacklist",
    "remove_from_blacklist",
    "list_blacklist",
]