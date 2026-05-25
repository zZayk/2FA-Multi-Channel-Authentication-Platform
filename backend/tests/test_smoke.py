"""Smoke tests — sanity check the wiring. Cheap, fast, no DB."""

from __future__ import annotations


async def test_health_returns_ok(client):
    """GET /health → 200 with status+version body."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


async def test_app_factory_isolated(app):
    """create_app() returns a configured FastAPI instance."""
    assert app.title == "2FA Multi-Channel Authentication Platform"
    assert app.version == "0.1.0"