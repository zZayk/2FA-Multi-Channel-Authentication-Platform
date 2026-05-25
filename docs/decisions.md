# Architecture Decisions Log

One-liner format: `YYYY-MM-DD | [topic] | [decision] | [reason]`

This log feeds the internship report. Append non-obvious technical choices only — not every line of code.

---

2026-05-20 | layout | layered backend (api/core/models/schemas/services/tasks) | swap channels/abuse engine without rewriting callers; FastAPI "Bigger Applications" convention
2026-05-25 | infra  | Celery Beat in its own container, never inside worker     | scaling worker → N would create N schedulers → duplicate periodic tasks
2026-05-25 | infra  | base docker-compose.yml + docker-compose.override.yml     | dev-only ergonomics (bind-mounts, hot-reload) stay out of the prod-shaped base
2026-05-25 | infra  | multi-stage backend Dockerfile (builder + runtime)        | smaller final image; no build toolchain in runtime layer
2026-05-25 | infra  | Redis used for OTP cache + Celery broker + rate-limit store | one infra component, three roles — minimises ops surface during internship
2026-05-25 | api    | lifespan asynccontextmanager over @app.on_event             | shared scope for DB/Redis/httpx clients; on_event deprecated in FastAPI ≥0.93
2026-05-25 | api    | JSON logs via python-json-logger on root + uvicorn loggers  | central log store parses fields directly; correlation_id will plug in cleanly
2026-05-25 | api    | create_app() factory pattern (not module-level app only)    | tests spin up isolated app instances with per-test config
2026-05-25 | config | Pydantic BaseSettings (pydantic-settings v2) over os.getenv | type coercion + validation + .env loading; fail-fast on bad config
2026-05-25 | config | SecretStr for keys/passwords                                | accidental str()/repr()/logs print "**********" instead of the value
2026-05-25 | config | get_settings() memoised with @lru_cache                     | one Settings() per process; tests clear cache between cases
2026-05-25 | config | Celery broker on Redis DB 1, result backend on DB 2         | logical isolation from OTP cache (DB 0) — no key collisions, easier flush
2026-05-25 | db     | async SQLAlchemy 2.0 + asyncpg                              | event loop stays free under slow DB; matches FastAPI's async model
2026-05-25 | db     | per-request AsyncSession via get_session() Depends          | one transactional scope per request; no cross-request state leak
2026-05-25 | db     | pool_pre_ping=True on engine                                | kills stale conns transparently (NAT timeouts, restarts) at cost of 1 SELECT 1
2026-05-25 | model  | OTP.id = UUIDv4, not autoincrement int                      | no enumeration attack; safe to expose in URLs/logs
2026-05-25 | model  | OTP.code_hash (bcrypt), never plaintext                     | DB dump useless to attacker; mirrors password storage best practice
2026-05-25 | model  | OTP.correlation_id UUID indexed, defaulted server/client    | enables single-trace lookup across api/service/task/channel logs
2026-05-25 | model  | DB-level Enum types for channel and status                  | DB rejects garbage values even via raw SQL — defence in depth
2026-05-25 | repo   | .env.example committed, .env ignored                        | onboarding doc + secret-free template; real secrets via vault/SOPS in prod
2026-05-25 | repo   | README architecture diagram in ASCII                        | renders everywhere (GitHub, terminal, PDF) — no broken image links in report
