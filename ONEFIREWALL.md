# Browser Agent Toolkit — project memory

## What this is
A Python backend that gives an LLM hands on a real browser (Playwright →
Chromium) and tests websites. It is **not** a web app: it is a service/library.

## Commands
- Install: `pip install -r requirements.txt && python -m playwright install chromium`
- Run REST API: `uvicorn app.main:app --port 8080`
- Run MCP (stdio): `python -m app.mcp_server`
- Run MCP (HTTP): `MCP_TRANSPORT=streamable-http python -m app.mcp_server`
- Tests: `python -m pytest -q`
- Compile check: `python -m compileall app tests`

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
