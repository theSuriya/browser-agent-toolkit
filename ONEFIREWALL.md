# Browser Agent Toolkit — project memory

## What this is
A Python backend that gives an LLM hands on a real browser (Playwright →
Chromium) and tests websites. It is **not** a web app: it is a service/library.

## Commands
- Install: `pip install -r requirements.txt && python -m playwright install chromium`
- Run REST API: `uvicorn app.main:app --port 8080`
- Run a dedicated job worker: `python -m app.worker`
- Run MCP (stdio): `python -m app.mcp_server`
- Run MCP (HTTP): `MCP_TRANSPORT=streamable-http python -m app.mcp_server`
- Tests: `python -m pytest -q`
- Browser-free tests (run anywhere): `python -m pytest tests/test_api.py tests/test_tenancy.py tests/test_metering.py tests/test_ratelimit.py tests/test_queue.py tests/test_storage.py tests/test_registry.py tests/test_jobs.py -q`
- Compile check: `python -m compileall app tests migrations`
- Migrations (production): `alembic upgrade head` with `DATABASE_URL` set to a Postgres DSN

## Architecture
- Backend-only. Three front doors over one browser layer: REST (FastAPI), MCP
  server, and the in-process agent loop.
- `app/browser.py` — one shared `Browser`; each session is an isolated
  `BrowserContext`. Sessions are per-request, capped by `MAX_SESSIONS`, and
  reaped after `SESSION_IDLE_SECONDS`.
- `app/actions.py` — atomic actions; `app/tools.py` — OpenAI tool specs and the
  single `run_tool` dispatcher (validates args, serialises per session,
  normalises errors to `{"ok": false, ...}`).
- `app/agent.py` — provider-agnostic loop over any OpenAI-compatible endpoint.
- Page is addressed by **refs** (`e1`, `e2`, …) from `app/snapshot.py`, not
  guessed CSS selectors.

## Product layer (multi-tenant + metered)
- Multi-tenant now: `organization -> org_members -> api_keys`, and every key inherits
  its org's plan. `Principal` carries `org_id` + `plan_code`.
- Persistence: async SQLAlchemy (`app/db.py`, `app/models.py`) — organizations, users,
  org_members, api_keys, plans, subscriptions, usage_events, jobs, artifacts,
  audit_log. `DATABASE_URL` (Postgres in prod); empty means SQLite from `DB_PATH`.
  `create_all` in the lifespan is dev/test only; production runs Alembic (`migrations/`).
- `app/plans.py` is the plan catalog (rate limit, session caps, agent steps, quota,
  overage), seeded into `plans` at startup. Billing unit = browser-seconds + agent-tasks,
  never API calls.
- `app/repository.py` = owner-scoped data access (always filter by `org_id`).
  `app/metering.py` = writes `usage_events` and enforces quotas (hard-cap plans return
  `402 quota_exceeded`; overage plans keep working and accrue a charge).
- Routes: `/accounts/signup` (public, self-serve), `/accounts/me`, `/accounts/keys`,
  `/usage`, plus `/ready` (DB readiness). `/admin/keys` is org-scoped.
- Metering is wired in `app/routers/{browser,agents,sitecheck}.py`: enforce before the
  work, meter after (browser-seconds, agent-tasks, agent-steps, sitecheck, screenshots).
- Auth/middleware is async now: `KeyStore.verify` is `await`ed in `AuthMiddleware`;
  the key-level `rate_limit` still overrides the plan's.

## Async jobs, worker and shared services (Phase 2)
- `POST /jobs` (202 {id}), `GET /jobs`, `GET /jobs/{id}` — async job API in
  `app/routers/jobs.py`. It validates the payload with the existing `AgentRequest`/
  `SiteCheckRequest` models, checks quota, writes a `jobs` row and enqueues an envelope.
- `app/worker.py` (`python -m app.worker`) consumes the queue: per-tenant fairness
  (defers a job when an org is at `JOBS_MAX_CONCURRENT_PER_ORG`), retries a crashed job
  once (`JOB_MAX_ATTEMPTS`), meters its usage, and fires an HMAC-signed webhook
  (`X-BAT-Signature`) on terminal state. `Worker.executor` is injectable (tests pass fakes).
- `app/jobs.py` = job persistence, status transitions (`mark_running`/`mark_finished`),
  `envelope`, lease-based `reap_stale`, and `deliver_webhook`.
- Redis-backed services, all falling back to in-process when `REDIS_URL` is empty:
  `app/cache.py` (`make_redis`), `app/ratelimit.py` (`RedisSlidingWindowLimiter` +
  `build_limiter`), `app/queue.py` (`RedisJobQueue`/`InProcessJobQueue`),
  `app/registry.py` (`SessionRegistry`, worker attribution for live sessions).
- `app/storage.py` = artifact store: `S3ArtifactStore` (presigned) or
  `LocalArtifactStore` (HMAC-signed expiring URLs, path-traversal guarded); served by
  `GET /artifacts/{key}` (public path, signature-gated). `build_store` picks by `S3_BUCKET`.
- `app/main.py` wires limiter/queue/registry/store into `app.state`, runs an inline worker
  when `RUN_INLINE_WORKER` (default true), reaps stale jobs, and `/ready` reports database
  and redis. `docker-compose.yml` has a dedicated `worker` service (`--scale worker=N`).
- Migration `0002_phase2_jobs` adds `jobs.attempts/worker_id/started_at`.
- Tests use `fakeredis` (in-process) and a real `redis` client against a fakeredis TCP
  server for the live check; no Redis server or browser needed.

## Conventions
- Python 3.10+, type hints, pydantic v2 for all I/O, one error shape
  `{"error": {"code", "message", "details?"}}`.
- Secrets only from `.env` (`config.py` / `Settings`); every var is in
  `.env.example`.
- Tools return `{"ok": ...}` dicts and never leak stack traces.
- Never run `playwright install --with-deps` in the sandbox; the host needs
  `libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 libxdamage1
  libxkbcommon0 libxcomposite1 libxrandr2 libgbm1 libpango-1.0-0 libcairo2
  libasound2`.
- MCP: installed SDK is v1 (`from mcp.server.fastmcp import FastMCP`). `run()`
  takes **no** `host`/`port` — set `mcp.settings.host/port`. Client API is
  `from mcp import ClientSession` + `mcp.client.streamable_http.streamablehttp_client`.
- This SDK's `FastMCP` crashes on stringified annotations, so `app/mcp_server.py`
  must NOT use `from __future__ import annotations`.

## Tests
- `tests/conftest.py` gives each test its **own** `BrowserManager` (fresh event
  loop per test) — the global manager would bind to the first test's loop and
  hang. Keep per-test managers.
- `tests/fixtures/site/` holds `good.html`, `bad.html`, `page2.html` served by a
  local `http.server`; `bad.html` intentionally has an unlabelled input, an
  `img` without `alt`, and a dead link.
- `tests/test_agent_loop.py` drives the loop with a scripted fake LLM, so no
  network/API key is needed.
- `tests/test_tenancy.py` (signup, per-org key isolation) and `tests/test_metering.py`
  (usage sums, hard-cap 402, overage estimation) need no browser and run in any sandbox.
- Browser-free Phase 2 suites: `tests/test_ratelimit.py`, `tests/test_queue.py`,
  `tests/test_storage.py`, `tests/test_registry.py` (fakeredis) and `tests/test_jobs.py`
  (202 contract, org isolation, 422, worker success + usage + webhook, retry-once,
  terminal failure, webhook signing) — no Redis server or browser required.
- The sandbox here has no Chromium OS libraries (`playwright install --with-deps` is
  forbidden by convention), so browser tests are skipped/failed locally — that is
  environmental, not a code failure. Run them on a host with the libs.
