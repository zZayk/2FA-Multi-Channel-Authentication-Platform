# Architecture Decisions Log

Append non-obvious technical choices here. This log feeds the internship report.

## Format

Group by date (newest first). One entry per decision:

```markdown
## YYYY-MM-DD

- **[topic]** Short decision (≤ 80 chars).
  _Reason:_ Why this choice over the obvious alternative.
```

Topics used so far: `repo`, `layout`, `infra`, `api`, `config`, `db`, `model`, `ci`, `tests`, `tasks`.

---

## 2026-05-25

### CI & tests

- **[ci]** CI runs ruff + pytest + docker build + commitlint on every PR.
  _Reason:_ Green-light contract; commitlint forces Conventional Commits feeding `CHANGELOG`-friendly history.

- **[tests]** Env vars set in `conftest.py` *before* importing app modules.
  _Reason:_ `get_settings()` is `@lru_cache`'d — late env edits never reach the cached instance.

- **[tests]** HTTPX `ASGITransport` over real `localhost` requests.
  _Reason:_ No network, no port races; tests run in parallel cleanly.

### Database & migrations

- **[db]** Alembic from day one, not `Base.metadata.create_all`.
  _Reason:_ Reversible, versioned, prod-safe schema changes; small upfront cost beats painful retrofit.

- **[db]** `alembic/env.py` uses async engine + `connection.run_sync` bridge.
  _Reason:_ Matches FastAPI async stack; official Alembic cookbook recipe.

- **[db]** First migration handcrafted, not `--autogenerate`.
  _Reason:_ Clearer pedagogical artifact; subsequent migrations can use autogen + hand-edit.

- **[db]** Async SQLAlchemy 2.0 + asyncpg driver.
  _Reason:_ Event loop stays free under slow DB; matches FastAPI's async model.

- **[db]** Per-request `AsyncSession` via `get_session()` Depends.
  _Reason:_ One transactional scope per request; no cross-request state leak.

- **[db]** `pool_pre_ping=True` on engine.
  _Reason:_ Kills stale connections transparently (NAT timeouts, restarts) at cost of one `SELECT 1`.

### Model

- **[model]** `OTP.id` is UUIDv4, not autoincrement int.
  _Reason:_ No enumeration attack; safe to expose in URLs / logs.

- **[model]** `OTP.code_hash` stores bcrypt, never plaintext.
  _Reason:_ DB dump useless to attacker; mirrors password storage best practice.

- **[model]** `OTP.correlation_id` UUID, indexed, defaulted server- and client-side.
  _Reason:_ Single-trace lookup across api / service / task / channel logs.

- **[model]** DB-level Enum types for `channel` and `status`.
  _Reason:_ DB rejects garbage values even via raw SQL — defence in depth.

### API & config

- **[api]** Lifespan asynccontextmanager over `@app.on_event`.
  _Reason:_ Shared scope for DB / Redis / httpx clients; `on_event` deprecated in FastAPI ≥ 0.93.

- **[api]** JSON logs via `python-json-logger` on root + `uvicorn.*` loggers.
  _Reason:_ Central log store parses fields directly; `correlation_id` will plug in cleanly.

- **[api]** `create_app()` factory pattern, not module-level `app` only.
  _Reason:_ Tests spin up isolated app instances with per-test config.

- **[config]** Pydantic `BaseSettings` (pydantic-settings v2) over `os.getenv`.
  _Reason:_ Type coercion + validation + `.env` loading; fail-fast on bad config.

- **[config]** `SecretStr` on all keys / passwords.
  _Reason:_ Accidental `str()` / `repr()` / logs print `"**********"` instead of the value.

- **[config]** `get_settings()` memoised with `@lru_cache`.
  _Reason:_ One `Settings()` per process; tests clear cache between cases.

- **[config]** Celery broker on Redis DB 1, result backend on DB 2.
  _Reason:_ Logical isolation from OTP cache (DB 0) — no key collisions, easier flush.

### Infrastructure

- **[infra]** Celery Beat in its own container, never inside worker.
  _Reason:_ Scaling worker → N would create N schedulers → duplicate periodic tasks.

- **[infra]** Base `docker-compose.yml` + `docker-compose.override.yml`.
  _Reason:_ Dev-only ergonomics (bind-mounts, hot-reload) stay out of the prod-shaped base.

- **[infra]** Multi-stage backend Dockerfile (builder + runtime).
  _Reason:_ Smaller final image; no build toolchain in runtime layer.

- **[infra]** Redis used for OTP cache + Celery broker + rate-limit store.
  _Reason:_ One infra component, three roles — minimises ops surface during internship.

### API router & tests

- **[api]** Router is thin: parse → service call → response. No SQL/hashing/business logic.
  _Reason:_ Service tests don't need HTTP; router tests cover wiring only — cleaner test pyramid.

- **[tests]** Week 2 tests cover pure helpers + schema validation only.
  _Reason:_ Model uses `postgresql.UUID`; SQLite can't represent it without TypeDecorator. Postgres integration via testcontainers lands Week 3.

### Service layer

- **[api]** OTP code generated via `secrets.randbelow`, not `pyotp.TOTP`.
  _Reason:_ Delivery OTPs are one-shot out-of-band codes, not authenticator-app TOTPs; `secrets` is OS CSPRNG.

- **[api]** `passlib.CryptContext` over raw `bcrypt`.
  _Reason:_ Algorithm upgrade path — new schemes appended later; old hashes keep verifying.

- **[config]** `BCRYPT_ROUNDS` setting; 12 prod, 4 tests via env override.
  _Reason:_ OWASP-recommended cost in prod; pytest stays snappy without sacrificing correctness.

- **[api]** Service owns `await session.commit()`; router never commits.
  _Reason:_ Business operation = one transaction; pushing commit to router risks half-applied state.

- **[api]** Plaintext OTP returned from `create_otp` exactly once, then discarded.
  _Reason:_ Only the hash persists; channel adapter consumes the plaintext for delivery, then it falls out of scope.

- **[api]** Max-attempts and TTL both transition status to `EXPIRED`.
  _Reason:_ Two paths, same observable outcome; avoids new enum value churn in v1.

### Schemas

- **[api]** `schemas/` distinct from `models/`; never return ORM instance directly.
  _Reason:_ Prevents leaking `code_hash` / internal columns; mass-assignment defence.

- **[api]** `extra="forbid"` on all request schemas.
  _Reason:_ Reject unknown fields with 422 — typos surface fast, no silent ignore.

- **[api]** Channel-aware recipient validator (E.164 vs email) at schema layer.
  _Reason:_ Wrong shape → 422 at boundary, not 500 from a downstream channel adapter.

- **[api]** OTPCreatedResponse never echoes the plaintext code.
  _Reason:_ Code travels only via SMS/Email; HTTP response would defeat the channel separation.

### Tasks (Celery)

- **[tasks]** JSON serializer, never pickle.
  _Reason:_ Pickle on a compromised broker = RCE; JSON is data-only.

- **[tasks]** `task_acks_late=True` + `worker_prefetch_multiplier=1`.
  _Reason:_ Survive worker crash mid-task; fairer distribution once channel I/O is slow.

- **[tasks]** `autodiscover_tasks(["app.tasks"])` — no manual `imports=` list.
  _Reason:_ New task module = drop file in `app/tasks/`, no registry edit.

- **[tasks]** Beat schedule starts empty; `poll_pending` lives as a stub.
  _Reason:_ Containers boot now; real schedule entry added in Week 5 with no path churn.

### Repo

- **[repo]** `.env.example` committed, `.env` ignored.
  _Reason:_ Onboarding doc + secret-free template; real secrets via vault / SOPS in prod.

- **[repo]** README architecture diagram in ASCII.
  _Reason:_ Renders everywhere (GitHub, terminal, PDF) — no broken image links in report.

## 2026-05-20

- **[layout]** Layered backend (`api` / `core` / `models` / `schemas` / `services` / `tasks`).
  _Reason:_ Swap channels / abuse engine without rewriting callers; FastAPI "Bigger Applications" convention.