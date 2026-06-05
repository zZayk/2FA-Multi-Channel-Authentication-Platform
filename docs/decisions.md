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

- **[model]** `OTP.code_hash` stores HMAC-SHA256 hex digest, never plaintext.
  _Reason:_ DB dump alone is useless without `SECRET_KEY`; see Service-layer entry for the HMAC-vs-bcrypt rationale.

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

- **[tests]** Integration tests opt-in via `--run-integration` flag.
  _Reason:_ Default `pytest -q` stays fast and Docker-daemon-free in CI; opt-in path runs real Postgres for DB-backed assertions.

- **[tests]** Integration test DB schema built via `Base.metadata.create_all`, not Alembic.
  _Reason:_ Test-loop speed; separate migration-vs-model test will guard against drift (Week 4).

- **[tests]** Per-test isolation = `TRUNCATE ... RESTART IDENTITY CASCADE`, not recreate-schema.
  _Reason:_ ~100x faster than re-running create_all per test; CASCADE keeps FK ordering simple.

### Auth (API key)

- **[model]** API key stored as HMAC-SHA256(key, SECRET_KEY), 64-char hex.
  _Reason:_ Same pattern as OTPs — random CSPRNG keys, not human-chosen; DB dump alone useless without `SECRET_KEY`.

- **[model]** `key_prefix` (first 8 chars of plaintext) stored alongside hash.
  _Reason:_ Audit logs / admin UI can identify a key without revealing the secret; "Was it the `l2t_abc1...` key?" beats opaque UUIDs.

- **[model]** `revoked_at` nullable timestamp, not boolean `is_revoked`.
  _Reason:_ Timestamp gives forensic value (when was it killed?) at no extra cost; boolean loses info.

- **[model]** `last_used_at` updated debounced by the service layer, not every request.
  _Reason:_ Hot keys would hammer a single row otherwise; debounce keeps observability without write amplification.

- **[api]** Plaintext key format `l2t_<32 url-safe bytes>`.
  _Reason:_ Prefix is greppable in commits/logs (GitHub PAT-style); body is high-entropy CSPRNG. Routes future format migrations.

- **[api]** Key lookup by indexed `key_hash` equality (not full-table scan + per-row HMAC compare).
  _Reason:_ O(log n) via B-tree index; HMAC is deterministic so equality lookup is sound.

- **[api]** Auth as FastAPI `Depends` (`require_api_key`), not global middleware.
  _Reason:_ Per-route opt-in keeps `/health` public; integrates with OpenAPI so Swagger renders an Authorize button.

- **[api]** `APIKeyHeader(auto_error=False)` + custom 401 with `WWW-Authenticate`.
  _Reason:_ FastAPI's built-in raises 403 before our code runs; we want a consistent 401 + clear message for missing vs invalid.

- **[api]** Swagger/ReDoc/openapi.json disabled when `ENVIRONMENT != dev`.
  _Reason:_ Interactive docs enumerate every endpoint + schema — useful in dev, attack-surface in prod. URLs set to None disables routes.

- **[api]** OpenAPI metadata (title/version/contact) sourced from Settings, not hardcoded.
  _Reason:_ Single source of truth; version bumps flow from one place into both `/health` and the spec.

- **[config]** App title/version read from `APP_NAME` / `APP_VERSION` settings.
  _Reason:_ Keeps `create_app()` env-driven; smoke tests still assert the defaults.

- **[tests]** Auth-gated unit tests use `app.dependency_overrides[require_api_key]`.
  _Reason:_ Schema-validation (422) tests must reach body validation without a real key/DB; override is the clean FastAPI seam.

- **[tests]** E2E test intercepts `dispatch_otp.delay` to capture the plaintext code.
  _Reason:_ Plaintext never crosses HTTP; intercepting dispatch is the only way the test can learn the code to verify it — without a live Celery/Redis.

- **[tests]** E2E overrides `get_session` to bind the app to the testcontainers engine.
  _Reason:_ Module-level engine points at the dev DB; override routes the running app at the ephemeral container for isolated assertions.

- **[db]** `alembic/env.py` honours a pre-set `sqlalchemy.url` over Settings.
  _Reason:_ Lets tests point Alembic at an ephemeral DB and ops use `-x`/ini overrides; Settings remains the default, not a hard override.

- **[tests]** Drift test applies migrations to a throwaway DB, diffs vs `Base.metadata` via `compare_metadata`.
  _Reason:_ Catches "edited model, forgot migration" before prod. Fails naming the offending table/column.

### Service layer

- **[api]** OTP code generated via `secrets.randbelow`, not `pyotp.TOTP`.
  _Reason:_ Delivery OTPs are one-shot out-of-band codes, not authenticator-app TOTPs; `secrets` is OS CSPRNG.

- **[api]** OTP storage uses HMAC-SHA256 keyed with `SECRET_KEY`, not bcrypt.
  _Reason:_ bcrypt defends against offline brute-force of *human-chosen* passwords. OTPs are CSPRNG 6-digit codes, short TTL, max-attempts capped at API layer. DB dump alone is useless without `SECRET_KEY`. HMAC also sidesteps the bcrypt 4.x breakage that bit `passlib`.

- **[api]** Service owns `await session.commit()`; router never commits.
  _Reason:_ Business operation = one transaction; pushing commit to router risks half-applied state.

- **[api]** Plaintext OTP returned from `create_otp` exactly once, then discarded.
  _Reason:_ Only the hash persists; channel adapter consumes the plaintext for delivery, then it falls out of scope.

- **[api]** Max-attempts and TTL both transition status to `EXPIRED`.
  _Reason:_ Two paths, same observable outcome; avoids new enum value churn in v1.

### Channels

- **[api]** `ChannelAdapter` ABC + registry under `services/channels/`.
  _Reason:_ Strategy pattern — dispatch depends on abstraction; adding WhatsApp/Push = drop a file, no edits to service or task.

- **[api]** `TransientChannelError` vs `PermanentChannelError` split.
  _Reason:_ Celery `autoretry_for=(TransientChannelError,)` retries blips; permanent failures surface immediately, no retry storm on bad data.

- **[api]** `SendOutcome` enum (ACCEPTED / REJECTED / RETRY) returned, not raised.
  _Reason:_ Normal outcomes are values; exceptions reserved for "the call itself broke". Easier dispatch-side branching.

- **[api]** SMS adapter builds a fresh `httpx.AsyncClient` per `send()` call.
  _Reason:_ Celery's `asyncio.run` per-task spins a fresh loop; long-lived clients tied to a dead loop raise. Lose pooling, gain correctness. Revisit if volume forces it.

- **[api]** SMS adapter raises `PermanentChannelError` on 401/403, not REJECTED.
  _Reason:_ Auth failure is an ops problem (rotate keys), not a per-message issue. Loud-fast surface beats silent reject-then-retry.

- **[api]** `client_ref` = `correlation_id` echoed into TunisiaSMS payload.
  _Reason:_ Provider echoes it in DLR webhook → links DLR back to our OTP row without a sender-ID-collision risk.

- **[api]** `aiosmtplib` (async) over stdlib `smtplib` (sync).
  _Reason:_ Fits FastAPI async stack; avoids `run_in_executor` thread overhead per send.

- **[api]** Email adapter generates its own `Message-ID`, reuses as `provider_message_id`.
  _Reason:_ SMTP has no provider-side message-id like SMS; an ours-generated stable ID still works for log correlation.

- **[api]** SMTP 4xx → `TransientChannelError` (retry); 5xx → `REJECTED` (permanent).
  _Reason:_ Per RFC 5321 §4.2.1 — 4xx is "temp failure, try again", 5xx is "permanent failure".

- **[api]** `subject: str | None = None` added to base interface; SMS ignores.
  _Reason:_ Email needs a subject; widening interface preserves single-call shape across channels. Default subject for v1.

- **[api]** Jinja2 templates live under `app/templates/{channel}/...j2`.
  _Reason:_ Copy churn (legal, locale, branding) decoupled from code; ops edits files, not Python. Locale subdirs (`fr/`, `ar/`) added later without refactor.

- **[api]** Single module-level Jinja `Environment` instance.
  _Reason:_ Bytecode cache persists across renders; re-creating per call would discard it.

- **[api]** `StrictUndefined` on the Jinja Environment.
  _Reason:_ Typos like `{{ cdoe }}` raise instead of silently rendering empty — caught in dev, not via support tickets.

### Dispatch & retries

- **[tasks]** Celery task body wraps `asyncio.run(...)` around async core.
  _Reason:_ Celery is sync; adapters + ORM are async. One loop per task call matches per-call adapter clients.

- **[tasks]** `autoretry_for=(TransientChannelError,)` + exponential backoff + jitter.
  _Reason:_ Retry only retryable errors; jitter avoids thundering-herd against a flapping upstream.

- **[tasks]** Plaintext OTP travels via Celery args (in Redis broker) at v1.
  _Reason:_ Simplest path; broker is internal. Prod hardening: pass `otp_id` only, task reads code from short-TTL encrypted Redis key. Tracked for Month 4.

- **[tasks]** `dispatch_otp` is idempotent for non-PENDING rows (no-op).
  _Reason:_ Replay safety — manual re-enqueue or Celery retry after partial commit won't double-send.

- **[tasks]** `include=[...]` over `autodiscover_tasks` for task module registration.
  _Reason:_ `autodiscover_tasks` looks for `tasks.py` per package by default; our modules use functional names (`dispatch.py`, `dlr.py`). Explicit list = no surprises.

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