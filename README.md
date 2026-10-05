# Browser Agent Toolkit

Give an LLM hands on a real browser. This is a Python backend that exposes a
Playwright-driven Chromium as **tools an LLM can call**, plus a **website test
harness** for the "the model built a site — run and test it" case.

One browser layer, three front doors:

```
LLM / IDE / your app
   ├── REST API        app/main.py            → sessions, /agents/run, /sitecheck
   └── MCP server      app/mcp_server.py       → the same actions as MCP tools
              │
        agent loop (app/agent.py)   perceive → decide → act → observe
              │
        tool layer (app/tools.py)   OpenAI-style specs + one dispatcher
              │
        browser layer (app/browser.py, app/actions.py)   18 atomic actions
              │
        Playwright → Chromium   (ref-addressed DOM snapshot)
```

The page is exposed as a **ref-addressed snapshot** (`e1`, `e2`, …): the model
clicks `ref="e3"` instead of guessing a CSS selector, which is what makes the
loop robust.

## Setup

```bash
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env          # then edit it
```

The only required `.env` value for the agent is `LLM_API_KEY` (any
OpenAI-compatible endpoint works: OpenAI, OpenRouter, Groq, Ollama, vLLM).

## Run

```bash
cp .env.example .env                    # set API_KEYS (an admin key); LLM_API_KEY for agents
uvicorn app.main:app --port 8080        # REST API + MCP at /mcp + docs at /docs
python -m app.mcp_server                # MCP over stdio for a local desktop host
```

Both the REST API and the MCP endpoint require an API key by default
(`REQUIRE_AUTH=true`). Set `REQUIRE_AUTH=false` for local development only.

## See it work (two demos)

Watch the browser get automated, step by step, with a screenshot at each stage
(written to `artifacts/demo/`):

```bash
python examples/demo.py                       # scripted: open Wikipedia, search, read
python examples/demo.py --url https://books.toscrape.com --query "fiction"
python examples/demo.py --headful             # show the actual window (needs a display)
```

Drive it with an LLM instead — the model decides the steps:

```bash
export LLM_API_KEY=sk-...
python examples/demo.py --mode agent \
  --task "Go to https://en.wikipedia.org, search for 'browser automation', and summarize the first paragraph"
```

The same task can be run through the REST API (`POST /agents/run`).

## REST API

| Method | Path | Purpose |
|---|---|---|
| POST | `/sessions` | create a browser session → `{id}` |
| GET | `/sessions` | list live sessions |
| DELETE | `/sessions/{id}` | close a session |
| POST | `/sessions/{id}/actions` | run one tool: `{"tool": "click", "arguments": {"ref": "e3"}}` |
| GET | `/sessions/{id}/console` | console messages, page errors, failed requests |
| GET | `/sessions/{id}/screenshot` | PNG of the current page |
| POST | `/agents/run` | run a natural-language task |
| POST | `/sitecheck` | load a URL and test it |
| GET | `/tools` | list every tool the agent can call |

Run a task:

```bash
curl -X POST localhost:8080/agents/run -H 'content-type: application/json' \
  -d '{"task": "Go to news.ycombinator.com and return the top story title."}'
```

Test a site you just built:

```bash
curl -X POST localhost:8080/sitecheck -H 'content-type: application/json' \
  -d '{"url": "http://localhost:3000", "required_text": ["Welcome"]}'
```

## Authentication (API keys)

Every request needs `Authorization: Bearer <key>`. Keys live **hashed** (SHA-256)
in a small SQLite database (`DB_PATH`, default `data/keys.db`) and are protected
by one ASGI middleware in front of the whole app — REST routes and the `/mcp`
endpoint share the same auth.

Bootstrap an admin key with `API_KEYS` (comma-separated), then mint customer keys:

```bash
# .env: API_KEYS=my-admin-key
curl -X POST localhost:8080/admin/keys -H 'Authorization: Bearer my-admin-key' \
  -H 'content-type: application/json' -d '{"name":"customer-a"}'
# -> {"id":"...","name":"customer-a","key":"bat_...","scopes":["use"]}   (shown once)
```

| Method | Path | Purpose |
|---|---|---|
| POST | `/admin/keys` | mint a key (admin only) → plaintext shown once |
| GET | `/admin/keys` | list keys, no secrets (admin only) |
| DELETE | `/admin/keys/{id}` | revoke a key (admin only) |

Each key gets its **own** browser sessions: another key cannot list, drive or
close your session (it is reported as `404`, so existence is not leaked), and
there are per-key caps (`MAX_SESSIONS_PER_KEY`) and rate limits
(`RATE_LIMIT_PER_MIN`, overridable per key).

## Connect a client

**REST** — send the key on every call:

```bash
curl localhost:8080/sessions -H 'Authorization: Bearer bat_...'
```

**Remote MCP (HTTP)** — the endpoint is `https://your-host/mcp` and the client
sends the key as a bearer header.

Claude Desktop / connectors (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "browser": {
      "type": "http",
      "url": "https://your-host/mcp",
      "headers": { "Authorization": "Bearer bat_..." }
    }
  }
}
```

Cursor (`.cursor/mcp.json`) and most clients use the same shape:

```json
{ "mcpServers": { "browser": { "url": "https://your-host/mcp", "headers": { "Authorization": "Bearer bat_..." } } } }
```

Raw Python client:

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client("https://your-host/mcp",
        headers={"Authorization": "Bearer bat_..."}) as (r, w, _):
    async with ClientSession(r, w) as client:
        await client.initialize()
        tools = await client.list_tools()          # 22 tools
        await client.call_tool("browser_navigate", {"url": "https://example.com"})
```

**Local MCP (stdio)** — for a single user's own machine, no key needed:

```json
{ "mcpServers": { "browser": { "command": "python", "args": ["-m", "app.mcp_server"] } } }
```

## Deploy

```bash
cp .env.example .env            # set API_KEYS=...
docker compose up --build       # API on :8080, MCP at /mcp
```

The image installs Chromium with its system libraries and runs as a non-root
user. Chromium is memory-hungry — give the host ~1–2 GB per browser and keep
`MAX_SESSIONS` in step. Put a TLS proxy in front (`deploy/Caddyfile` is a
ready-to-edit Caddy config that terminates HTTPS for `your-domain/mcp`).

## Tools the LLM can call

`navigate` · `snapshot` · `click` · `type_text` · `press_key` · `select_option`
· `hover` · `scroll` · `wait_for` · `get_text` · `get_html` · `get_attribute`
· `eval_js` · `get_links` · `get_console` · `screenshot` · `go_back` ·
`go_forward` · `reload`

The same set is exposed over MCP as `browser_*` tools, plus two high-level
tools: `site_check` and `browser_run_task`.

## Wiring your own LLM

Point it at the REST API, or call `run_agent` directly:

```python
from app.agent import run_agent
from app.browser import manager

session = await manager.create_session()
outcome = await run_agent(session, "Find the price of the first product.")
print(outcome["result"])
```

`run_agent` is provider-agnostic — it speaks the OpenAI `tools` /
`tool_calls` format, so any compatible model works. To plug in an agent
framework, register `app.tools.run_tool` as your tool executor.

## Tests

```bash
python -m pytest -q
```

26 tests run a real Chromium against a local fixture site: the actions, the site
harness, the agent loop (driven by a scripted fake LLM, no API key needed), and
the auth suite (401 without a key, cross-key session isolation, revoked keys,
rate limiting).

## Layout

```
app/
  config.py      settings (.env)
  errors.py      one JSON error shape
  auth.py        hashed API keys (SQLite), principals, FastAPI dependencies
  middleware.py  auth + rate limit + security headers + access log (ASGI)
  ratelimit.py   per-key sliding-window limiter
  snapshot.py    in-page JS that builds the ref-addressable snapshot
  browser.py     shared browser + isolated per-key session contexts
  actions.py     18 atomic actions
  tools.py       OpenAI tool specs + dispatcher
  llm.py         minimal OpenAI-compatible async client
  agent.py       the perceive→act→observe loop
  sitecheck.py   the website test harness
  main.py        FastAPI app (mounts the MCP server at /mcp)
  mcp_server.py  MCP server (23 tools, per-key sessions)
  routers/       browser, agents, sitecheck, admin
tests/           pytest suite + local fixture site
examples/        run_agent.py, mcp_client.py, demo.py
deploy/          Caddyfile (TLS reverse proxy)
```
