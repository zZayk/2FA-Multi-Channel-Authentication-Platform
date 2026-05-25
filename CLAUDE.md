# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository State

This is a **4-month internship project at L2T Tunisie** building a production-grade 2FA multi-channel authentication platform (SMS + Email) with an anti-abuse engine and admin dashboard. The repo is currently in **pre-scaffold state** — only `README.md` and `docs/CLAUDE_INIT_PROMPT.md` exist. The init prompt in `docs/` is the authoritative source for planned architecture, phase plan, and working agreements until code lands.

If asked to start work, read `docs/CLAUDE_INIT_PROMPT.md` first — it contains the full 16-week breakdown and step-by-step bootstrap instructions.

## Dual Mission (load-bearing — overrides default Claude Code behavior)

This project has two equally-weighted objectives:

1. **TEACH the user.** The user is an intern building developer skills. For non-trivial code:
   - Add `[LEARN]` comment blocks explaining **why** the design decision was made, what pattern is being applied (name it: "Repository Pattern", "Celery chord", "Isolation Forest", etc.), and one concrete thing to read for depth.
   - Flag unfamiliar patterns with `// [NEW CONCEPT]` and name the pattern.
   - Answer questions at two levels: **SHORT** (direct unblock) and **DEEP** (only when user says "explain more").
   - This **overrides** the default "no comments" rule — pedagogical comments are required here.

2. **DELIVER in 4 months with a light workload.** Ship thin vertical slices first. Scaffold/generate anything that can be safely automated. Warn proactively about choices that will cost time later. Each module gets a "done" checklist.

## Working Agreement (Learning Contract)

1. **Explain before scaffold.** Before generating significant code, write one sentence: *"We are about to implement X. Key concept is Y. Plan: [bullets]."* Then generate.
2. **Checkpoint questions.** End sessions with 1–2 short understanding-check questions (e.g., "How would you add a new channel?").
3. **Complexity ladder.** Start with `v1 — minimal`. Move to `v2 — production-ready` only when asked, and explain what changed.
4. **Never just fix — explain the bug.** Always state (a) what the bug is, (b) root cause, (c) how to prevent that class.
5. **Architecture decisions log.** Append one-liners to `docs/decisions.md` for non-obvious choices: `YYYY-MM-DD | [topic] | [decision] | [reason]`. This feeds the internship report.

## Planned Stack

- **Backend:** FastAPI (async), SQLAlchemy async, Pydantic BaseSettings for config
- **Data:** PostgreSQL 15, Redis 7 (OTP cache + Celery broker + rate limit store)
- **Async:** Celery worker + Celery Beat (DLR polling scheduler — Beat must run as a separate container)
- **Channels:** TunisiaSMS API (SMS + DLR webhooks), SMTP/SendGrid (Email)
- **ML:** scikit-learn Isolation Forest for anomaly detection in the anti-abuse engine
- **Frontend:** React (Chart.js for SMS-vs-Email perf, heatmap for suspicious activity)
- **Infra:** Docker Compose (`docker-compose.yml` + `docker-compose.override.yml` for dev hot-reload)

## Planned Architecture

Layered backend under `backend/app/`:

```
api/        FastAPI routers (HTTP boundary)
core/       config (Pydantic BaseSettings), security, dependencies, database engine
models/     SQLAlchemy ORM models
schemas/    Pydantic request/response schemas
services/   Business logic (OTP lifecycle, channel send/fallback, abuse scoring)
tasks/      Celery async tasks (DLR polling, fallback dispatch)
main.py     FastAPI entry point with lifespan handler + JSON logging + CORS
```

Key cross-cutting concerns to preserve as the code lands:

- **OTP storage:** hash OTPs at rest (never plaintext). TTL + single-use + max-attempts enforced in service layer.
- **`correlation_id`** flows through OTP records and logs to trace a single auth attempt across api → service → task → channel adapter.
- **DLR loop:** Celery Beat polls TunisiaSMS DLR status → updates `status_history` → triggers Email fallback on `FAILED`/timeout.
- **Anti-abuse engine** runs as a service called from the OTP send path: rule-based scoring first, Isolation Forest second, explainable block decisions, blacklist short-circuits.

## Phase Plan (4 months)

- **Month 1 — Foundation & 2FA Core:** repo + Docker skeleton → pyotp OTP gen → multi-channel send + templates → REST API + Swagger + integration tests.
- **Month 2 — DLR & Anti-Abuse:** Beat-driven DLR polling → delivery metrics + auto-fallback → behavioral profiling (volume/freq/GeoIP) → Isolation Forest + blacklists.
- **Month 3 — Dashboard:** React setup → Chart.js perf views → heatmap + real-time alerts → PDF/CSV incident reports.
- **Month 4 — Hardening:** rate limiting + CAPTCHA + secure API keys → Locust load tests + Redis tuning → OWASP A01–A10 audit → final Compose polish + demo.

## Commands

Week 1 scaffold is in place. Canonical entry point:

```bash
cp .env.example .env       # first run only — fill in SECRET_KEY
docker compose up --build  # api:8000 · frontend:3000 · db:5432 · redis:6379
```

`docker-compose.override.yml` adds bind-mounts + `uvicorn --reload` automatically; no extra flag needed in dev.

Single endpoint exposed so far: `GET /health` → `{"status":"ok","version":"0.1.0"}`.

Lint, migration, and test runners arrive in later weeks — update this section the moment they land.

## Environment

- Platform is **Windows 11** with Git Bash. Use Unix shell syntax (`/dev/null`, forward slashes) when invoking Bash. PowerShell also available.
- Working directory: `D:\PROJECTS\2FA-Multi-Channel-Authentication-Platform`.
