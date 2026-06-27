"""
FastAPI application entry point.

[LEARN]
This module wires the HTTP boundary. It owns:
  - lifespan (startup/shutdown hooks)
  - middleware (CORS today, more later: request ID, rate limit)
  - router registration (added in Week 4 when the OTP API arrives)
  - structured logging setup

Pattern applied: "Application Factory / single-entry composition root".
Read more: FastAPI "Bigger Applications" guide.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pythonjsonlogger import jsonlogger

from app.api import admin as admin_router
from app.api import otp as otp_router
from app.core.config import get_settings


# Tag metadata — renders as grouped sections + descriptions in Swagger UI.
_OPENAPI_TAGS = [
    {"name": "meta", "description": "Liveness / readiness probes. No auth."},
    {
        "name": "otp",
        "description": (
            "Issue and verify one-time passwords across SMS and Email. "
            "All endpoints require a valid `X-API-Key` header."
        ),
    },
    {
        "name": "admin",
        "description": "Anti-abuse denylist management. Requires `X-API-Key`.",
    },
]

_API_DESCRIPTION = """
Production-grade 2FA platform — multi-channel OTP delivery (SMS + Email)
with delivery tracking, auto-fallback, and an anti-abuse engine.

### Authentication
All `/otp/*` endpoints require an API key sent as the `X-API-Key` header.
Click **Authorize** and paste a key (`l2t_...`) to try the endpoints below.
""".strip()


# =============================================================================
# Logging setup
# =============================================================================

# [LEARN] Why JSON logs?
# In dev a human-readable line is fine. In prod, logs are shipped to a central
# store (Loki / ELK / CloudWatch) and queried. If each line is JSON, the store
# parses fields directly (level, msg, correlation_id, channel, latency_ms...).
# String logs force regex extraction, which is brittle and slow.
# Pattern: "Structured logging".
# Read more: 12-factor app §XI "Logs"; python-json-logger docs.

def _configure_logging() -> None:
    """Configure root logger to emit JSON on stdout."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Remove default handlers (uvicorn injects its own — we override them).
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(stream=sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)

    # Align uvicorn's loggers to the same handler so access logs are also JSON.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvlog = logging.getLogger(name)
        uvlog.handlers = [handler]
        uvlog.propagate = False


logger = logging.getLogger(__name__)


# =============================================================================
# Lifespan
# =============================================================================

# [LEARN] Lifespan vs @app.on_event
# Old (deprecated): @app.on_event("startup") / @app.on_event("shutdown") —
#   two separate functions, no shared scope, can't share resources via closures.
# New: a single async generator. Code before `yield` = startup, code after = shutdown.
#   Resources can be created at startup and closed deterministically at shutdown.
#   Plays well with async context managers (DB engine, Redis pool, httpx client).
# Read more: Starlette "Lifespan" docs; FastAPI 0.93 release notes.

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """App startup / shutdown hook. Owns long-lived resources."""
    _configure_logging()
    logger.info("app.startup", extra={"event": "startup", "version": app.version})

    # Future home: open DB engine, Redis pool, httpx client here.
    # app.state.db = ...
    # app.state.redis = ...

    try:
        yield
    finally:
        logger.info("app.shutdown", extra={"event": "shutdown"})
        # Future home: close DB engine, Redis pool, httpx client.


# =============================================================================
# Application factory
# =============================================================================

def create_app() -> FastAPI:
    """Build the FastAPI app. Kept as a function so tests can spin up fresh instances."""
    settings = get_settings()

    # [LEARN] Hide interactive docs outside dev.
    # Swagger/ReDoc enumerate every endpoint + schema — useful in dev, but in
    # prod they widen the attack surface (and leak internal shape to scanners).
    # Setting the URLs to None disables the routes entirely.
    docs_enabled = settings.ENVIRONMENT == "dev"

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=_API_DESCRIPTION,
        openapi_tags=_OPENAPI_TAGS,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        contact={"name": "L2T Tunisie", "email": "dev@l2t.tn"},
    )

    # [LEARN] CORS in dev: permissive so the React dev server on :3000 can call :8000.
    # In Month 4 (hardening) this becomes a strict allow-list from settings.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Liveness probe — used by Docker healthcheck, k8s, monitoring."""
        return {"status": "ok", "version": app.version}

    # --- Routers ------------------------------------------------------------
    app.include_router(otp_router.router)
    app.include_router(admin_router.router)

    return app


app = create_app()
