# 2FA Multi-Channel Authentication Platform

A production-grade two-factor authentication service supporting **SMS** (via
TunisiaSMS) and **Email** (via SMTP/SendGrid), with an integrated anti-abuse
engine and a real-time admin dashboard. Built during a 4-month internship at
**L2T Tunisie**.

The platform issues one-time passwords across channels, tracks delivery via
TunisiaSMS DLR (Delivery Receipts), auto-falls back from SMS to Email on
failure, and scores every send for abuse using rule-based heuristics plus an
Isolation Forest anomaly detector. An operator dashboard exposes performance
metrics, suspicious-activity heatmaps, and incident reports.

---

## Quick start

```bash
cp .env.example .env
# (edit .env — at minimum set SECRET_KEY)

docker compose up --build
```

Once up:

| Service        | URL                                |
|----------------|------------------------------------|
| API            | http://localhost:8000              |
| Swagger / docs | http://localhost:8000/docs         |
| Health probe   | http://localhost:8000/health       |
| Frontend (dev) | http://localhost:3000              |
| Postgres       | `localhost:5432` (`app/app/app`)   |
| Redis          | `localhost:6379`                   |

---

## Module status

| # | Module             | Status        | Target  |
|---|--------------------|---------------|---------|
| 1 | 2FA OTP Service    | 🚧 In progress | Month 1 |
| 2 | TunisiaSMS + DLR   | 🚧 In progress | Month 2 |
| 3 | Anti-Abuse Engine  | 🚧 In progress | Month 2 |
| 4 | Admin Dashboard    | 🚧 In progress | Month 3 |
| 5 | Security Layer     | 🚧 In progress | Month 4 |
| 6 | DevOps & Hardening | 🚧 In progress | Month 4 |

---

## Architecture

```
                ┌──────────────┐
                │   Frontend   │  React + Chart.js  (port 3000)
                │  (Dashboard) │
                └──────┬───────┘
                       │ REST
                       ▼
   ┌──────────────────────────────────────┐
   │              FastAPI API             │  (port 8000)
   │   api/  →  services/  →  models/     │
   └────────┬─────────────────┬───────────┘
            │                 │
            │ enqueue         │ ORM (async SQLAlchemy)
            ▼                 ▼
   ┌────────────────┐   ┌──────────────┐
   │     Redis      │   │  PostgreSQL  │
   │  ┌──────────┐  │   │      15      │
   │  │  cache   │  │   └──────────────┘
   │  │  broker  │  │
   │  │ ratelim  │  │
   │  └──────────┘  │
   └──┬─────────┬───┘
      │         │
      ▼         ▼
 ┌─────────┐ ┌──────────┐
 │ Celery  │ │  Celery  │   workers run send-SMS / send-Email
 │ Worker  │ │   Beat   │   beat schedules DLR polls (cron-like)
 └────┬────┘ └────┬─────┘
      │           │
      ▼           ▼
 ┌──────────────────────────┐
 │   Channel adapters       │
 │   • TunisiaSMS HTTP API  │
 │   • SMTP / SendGrid      │
 └──────────────────────────┘
```

### Key flows

- **OTP send:** API → abuse engine → service hashes code → enqueue task →
  channel adapter → store status. `correlation_id` follows the request
  end-to-end.
- **DLR loop:** Beat triggers polling every 30s → worker fetches TunisiaSMS
  status → updates `OTP.status` → on `FAILED`/timeout, fires Email fallback.
- **Abuse engine:** Called from send path. Rule-based scoring first
  (volume/frequency/geo). Isolation Forest second on enriched features.
  Blacklist short-circuits. All decisions are explainable.

---

## Repository layout

```
backend/
  app/
    api/        FastAPI routers (HTTP boundary)
    core/       config (Pydantic BaseSettings), database (async engine), security
    models/     SQLAlchemy ORM models
    schemas/    Pydantic request/response schemas
    services/   business logic (OTP, channel send/fallback, abuse scoring)
    tasks/      Celery async tasks (DLR polling, fallback dispatch)
    main.py     FastAPI app factory + lifespan + JSON logging + CORS
  tests/
  Dockerfile
  requirements.txt
frontend/
  src/{components,pages,hooks,services}/
  Dockerfile
docker-compose.yml
docker-compose.override.yml   # dev: bind-mounts, hot reload, debug ports
docs/
  CLAUDE_INIT_PROMPT.md       # original brief — phase plan + learning contract
  decisions.md                # architecture decisions log (feeds the report)
CLAUDE.md                     # guidance for Claude Code sessions
.env.example
```

---

## Phase plan (4 months)

| Month | Focus                  | Key deliverables                                                              |
|-------|------------------------|-------------------------------------------------------------------------------|
| 1     | Foundation & 2FA Core  | Docker skeleton · pyotp OTP gen · multi-channel send · REST API + Swagger     |
| 2     | DLR + Anti-Abuse       | DLR polling via Beat · auto-fallback · behavioural profiling · Isolation Forest |
| 3     | Dashboard              | React + Chart.js perf views · suspicious-activity heatmap · PDF/CSV reports   |
| 4     | Hardening              | Rate limiting · CAPTCHA · Locust load tests · OWASP A01–A10 audit · demo prep |

Full breakdown: see [docs/CLAUDE_INIT_PROMPT.md](docs/CLAUDE_INIT_PROMPT.md).

---

## Tech stack

- **Backend:** FastAPI · SQLAlchemy 2.0 (async) · Pydantic v2 · Celery + Beat
- **Data:** PostgreSQL 15 · Redis 7
- **Channels:** TunisiaSMS HTTP API · SMTP / SendGrid
- **ML:** scikit-learn Isolation Forest
- **Frontend:** React · Chart.js
- **Infra:** Docker Compose · multi-stage builds

---

## License & context

Internship project at **L2T Tunisie**, 4-month duration. Architecture decisions
are tracked in [docs/decisions.md](docs/decisions.md) to feed the final report.
