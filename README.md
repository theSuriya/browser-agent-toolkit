# Browser Agent Toolkit

Give an LLM hands on a real browser. This is a Python backend that exposes a
Playwright-driven Chromium as **tools an LLM can call**, and ships a **website
test harness** for the common case where a model generated a site and needs to
verify it actually works.

The toolkit is a service/library, not a web app. One browser layer sits behind
three front doors: a REST API, an MCP server, and an in-process agent loop.

```
LLM / IDE / your app
   ├── REST API        app/main.py            sessions, /agents/run, /sitecheck
   └── MCP server      app/mcp_server.py       the same actions as MCP tools
              │
        agent loop (app/agent.py)    perceive → decide → act → observe
              │
        tool layer (app/tools.py)    OpenAI-style specs + one dispatcher
              │
        browser layer (app/browser.py, app/actions.py)   19 atomic actions
              │
        Playwright → Chromium    (ref-addressed DOM snapshot)
```

Pages are addressed by **refs** (`e1`, `e2`, …) from a live DOM snapshot. The
model clicks `ref="e3"` instead of guessing a CSS selector, which is what keeps
the loop reliable as pages change.

## Why use it

An LLM can reason and write, but it cannot see a live page, click a control, or
check whether the site it just produced renders correctly. This toolkit turns
browser control into discoverable, callable tools so any MCP host or OpenAI-style
client can:

- **Read live pages** it was never trained on (current prices, headlines, docs).
- **Act** — click, type, submit, log in, paginate, fill forms.
- **Verify its own output** — `site_check` returns console errors, failed
  requests, broken links and a screenshot, so a generate → test → fix loop closes.
- **Delegate a whole task** — `browser_run_task` runs a natural-language goal and
  returns the finished answer.

## Requirements

- Python 3.10+
- Chromium (installed by Playwright in one command below)
- Linux hosts need Chromium's system libraries (see [Troubleshooting](#troubleshooting))
- An OpenAI-compatible LLM endpoint only if you want the natural-language agent;
  the 19 atomic browser tools need no model at all.

## Local quickstart

Every command below runs from the project root. On Windows use **PowerShell**
(inline `VAR=value` does not work there; use `$env:VAR = "value"`).

### 1. Install

```bash
python -m venv .venv
# Linux/macOS:  source .venv/bin/activate
# Windows:      .venv\Scripts\Activate.ps1

pip install -r requirements.txt
python -m playwright install chromium
```

### 2. Configure

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

Open `.env` and set two values:

- `API_KEYS` — your admin API key, required to reach the REST API and `/mcp`.
- `LLM_API_KEY` — optional; only the natural-language agent needs it.

```dotenv
API_KEYS=my-admin-key
LLM_API_KEY=sk-...           # optional
LLM_MODEL=gpt-4o-mini        # optional
```

Any OpenAI-compatible endpoint works: OpenAI, OpenRouter, Groq, Together, or a
local server (Ollama, vLLM) via `LLM_BASE_URL`.

### 3. Start the server

```bash
uvicorn app.main:app --port 8080
```

This serves the REST API, the interactive docs at `/docs`, and the MCP endpoint
at `/mcp` — all behind the same API-key auth.

### 4. Verify it works

```bash
curl -s localhost:8080/health
# {"status":"ok"}

curl -s localhost:8080/sessions -H 'Authorization: Bearer my-admin-key'
# {"sessions":[]}
```

If the second call returns `401`, the `Authorization` header is missing or the
key does not match `API_KEYS`.

## Ways to drive it locally

### A. Scripted demo (no LLM, no key) — fastest check

```bash
python examples/demo.py                     # open Wikipedia, search, read the result
python examples/demo.py --url https://books.toscrape.com --query "fiction"
python examples/demo.py --headful            # show the real window (needs a display)
```

It prints each step and saves a screenshot per stage to `artifacts/demo/`. This
proves the browser layer end to end before you involve a model.

### B. Natural-language agent (LLM decides the steps)

```bash
export LLM_API_KEY=sk-...                    # PowerShell: $env:LLM_API_KEY = "sk-..."
python examples/demo.py --mode agent \
  --task "Go to https://en.wikipedia.org, search for 'browser automation', and summarize the first paragraph"
```

You get a live step trace and the model's written answer:

```
AGENT TASK: Go to https://en.wikipedia.org, search for ...
   step 1: navigate({'url': 'https://en.wikipedia.org'}) -> ok=True
   step 2: snapshot({'include_text': True}) -> ok=True
   step 3: type_text({'ref': 'e3', 'text': 'browser automation', 'submit': True}) -> ok=True
   ...
AGENT ANSWER
   <the model's result>
   (4 steps)
```

`--headful` opens the window so you can watch it work. `--max-steps 40` raises
the step cap for longer tasks. The same task is available over HTTP as
`POST /agents/run`.

### C. REST API

Every request needs `Authorization: Bearer <key>`. Run a task and test a site:

```bash
# natural-language task
curl -X POST localhost:8080/agents/run \
  -H 'Authorization: Bearer my-admin-key' -H 'content-type: application/json' \
  -d '{"task": "Go to news.ycombinator.com and return the top story title."}'

# test a site you just built (console errors, failed requests, broken links, a11y)
curl -X POST localhost:8080/sitecheck \
  -H 'Authorization: Bearer my-admin-key' -H 'content-type: application/json' \
  -d '{"url": "http://localhost:3000", "required_text": ["Welcome"]}'
```

### D. MCP — the way an LLM host consumes it

There are two ways to run MCP locally.

**Standalone HTTP server (no API key — simplest).** Open two terminals:

```bash
# Terminal 1 — start the MCP server (serves http://127.0.0.1:8080/mcp)
MCP_TRANSPORT=streamable-http python -m app.mcp_server

# Terminal 2 — run the client, exactly as an LLM host would
python examples/mcp_client.py
```

Expected output:

```
connected to http://127.0.0.1:8080/mcp
available tools: ['browser_navigate', 'browser_snapshot', ..., 'site_check', 'browser_run_task']
navigate -> {'ok': True, 'url': 'https://example.com/', 'title': 'Example Domain', 'status': 200}
page title: Example Domain
```

If the port is busy, set `PORT` on the server and `MCP_SERVER_URL` on the client:

```bash
PORT=8099 MCP_TRANSPORT=streamable-http python -m app.mcp_server
MCP_SERVER_URL=http://127.0.0.1:8099/mcp python examples/mcp_client.py
```

**Through the full app (with API key).** Start the app as in step 3, then point
the client at `/mcp` with the key:

```bash
# terminal 1
uvicorn app.main:app --port 8080
# terminal 2
MCP_API_KEY=my-admin-key python examples/mcp_client.py
```

**Into a real AI host.** Add the server to the host's MCP config. For a local
stdio server (no URL, no key):

```json
{
  "mcpServers": {
    "browser": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/absolute/path/to/browser-agent-toolkit"
    }
  }
}
```

For the running HTTP server, use `url` and (if auth is on) a bearer header:

```json
{
  "mcpServers": {
    "browser": {
      "type": "http",
      "url": "http://127.0.0.1:8080/mcp",
      "headers": { "Authorization": "Bearer my-admin-key" }
    }
  }
}
```

The natural-language tool `browser_run_task` needs `LLM_API_KEY`; the other 21
tools work without any model.

## MCP tools (22)

The MCP server exposes the atomic actions plus three higher-level tools.

| Group | Tools |
|---|---|
| Navigate & perceive | `browser_navigate`, `browser_snapshot`, `browser_get_text`, `browser_get_html`, `browser_get_attribute`, `browser_get_links`, `browser_get_console`, `browser_screenshot` |
| Act | `browser_click`, `browser_type`, `browser_press_key`, `browser_select_option`, `browser_hover`, `browser_scroll`, `browser_wait_for` |
| History & lifecycle | `browser_go_back`, `browser_go_forward`, `browser_reload`, `browser_close` |
| Power tools | `browser_eval` (run JS in the page), `site_check` (full site test), `browser_run_task` (natural-language agent) |

Every tool returns JSON and reports errors as `{"ok": false, "error": ...}`
rather than raising, so an agent can re-snapshot and retry.

## REST API

| Method | Path | Purpose |
|---|---|---|
| POST | `/sessions` | create a browser session → `{id}` |
| GET | `/sessions` | list your live sessions |
| DELETE | `/sessions/{id}` | close a session |
| POST | `/sessions/{id}/actions` | run one tool: `{"tool": "click", "arguments": {"ref": "e3"}}` |
| GET | `/sessions/{id}/console` | console messages, page errors, failed requests |
| GET | `/sessions/{id}/screenshot` | PNG of the current page |
| POST | `/agents/run` | run a natural-language task |
| POST | `/sitecheck` | load a URL and test it |
| GET | `/tools` | list every tool the agent can call |
| GET | `/health` | liveness (no key required) |

`POST /agents/run` returns `{ok, result, steps, session_id, transcript}`. The
`transcript` is the full decision trail (each tool the model chose and the result
it got), which is what you inspect to audit an agent run. Pass `session_id` back
to continue in the same browser; it must belong to your key.

## Authentication

Every route except `/health` and `/docs` needs `Authorization: Bearer <key>`.
Keys are stored **hashed** (SHA-256) in a small SQLite database (`DB_PATH`,
default `data/keys.db`). A single ASGI middleware in front of the app covers both
the REST routes and the `/mcp` endpoint, so there is one auth path.

Bootstrap an admin key with `API_KEYS` (comma-separated), then mint customer keys:

```bash
curl -X POST localhost:8080/admin/keys \
  -H 'Authorization: Bearer my-admin-key' -H 'content-type: application/json' \
  -d '{"name":"customer-a"}'
# -> {"id":"...","name":"customer-a","key":"bat_...","scopes":["use"]}   (shown once, save it)
```

| Method | Path | Purpose |
|---|---|---|
| POST | `/admin/keys` | mint a key (admin scope only) → plaintext shown once |
| GET | `/admin/keys` | list keys, no secrets (admin scope only) |
| DELETE | `/admin/keys/{id}` | revoke a key (admin scope only) |

Isolation is enforced per key: another key cannot list, drive or close your
session (the attempt returns `404`, so existence is not leaked). Per-key limits
are `MAX_SESSIONS_PER_KEY` and `RATE_LIMIT_PER_MIN` (overridable per key).

## Connecting clients

**REST** — send the key on every call:

```bash
curl localhost:8080/sessions -H 'Authorization: Bearer bat_...'
```

**Claude Desktop / connectors** (`claude_desktop_config.json`):

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

**Cursor / Cline** (`.cursor/mcp.json`) use the same shape:

```json
{ "mcpServers": { "browser": { "url": "https://your-host/mcp", "headers": { "Authorization": "Bearer bat_..." } } } }
```

**Raw Python client:**

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client(
    "https://your-host/mcp", headers={"Authorization": "Bearer bat_..."}
) as (read, write, _):
    async with ClientSession(read, write) as client:
        await client.initialize()
        tools = await client.list_tools()                     # 22 tools
        await client.call_tool("browser_navigate", {"url": "https://example.com"})
```

## Wiring your own LLM

Point your agent at the REST API, or call `run_agent` in process:

```python
from app.agent import run_agent
from app.browser import manager

session = await manager.create_session()
outcome = await run_agent(session, "Find the price of the first product.")
print(outcome["result"])
```

`run_agent` speaks the OpenAI `tools` / `tool_calls` format, so any compatible
model works. To plug it into another agent framework, register
`app.tools.run_tool` as the tool executor.

## Deployment

```bash
cp .env.example .env          # set API_KEYS=...
docker compose up --build     # API on :8080, MCP at /mcp
```

The image installs Chromium with its system libraries and runs as a non-root
user. Chromium is memory-hungry: allow roughly 1–2 GB per browser and keep
`MAX_SESSIONS` in step. Put a TLS proxy in front; `deploy/Caddyfile` is a
ready-to-edit Caddy config that terminates HTTPS for `your-domain/mcp`.

## Configuration

All settings come from the environment or `.env` (see `.env.example`).

| Variable | Default | Purpose |
|---|---|---|
| `API_KEYS` | — | comma-separated bootstrap admin keys |
| `REQUIRE_AUTH` | `true` | enforce the bearer key on every route |
| `DB_PATH` | `data/keys.db` | SQLite file for hashed keys |
| `RATE_LIMIT_PER_MIN` | `120` | per-key request budget |
| `MAX_SESSIONS_PER_KEY` | `4` | concurrent browsers per key |
| `MAX_SESSIONS` | `8` | global concurrent browsers |
| `SESSION_IDLE_SECONDS` | `900` | idle sessions are reaped after this |
| `HEADLESS` | `true` | run Chromium without a visible window |
| `DEFAULT_TIMEOUT_MS` | `30000` | per-action timeout |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible endpoint |
| `LLM_API_KEY` | — | model key (agent only) |
| `LLM_MODEL` | `gpt-4o-mini` | model name |
| `AGENT_MAX_STEPS` | `25` | step cap for the agent loop |
| `MCP_HTTP_PATH` | `/mcp` | mount path of the MCP endpoint |

## Testing

```bash
python -m pytest -q
```

26 tests run a real Chromium against a local fixture site: the atomic actions,
the site harness, the agent loop (driven by a scripted fake LLM, so no API key or
network is needed), and the auth suite (401 without a key, cross-key session
isolation, revoked keys, rate limiting).

## Troubleshooting

**The browser will not launch on Linux.** Chromium needs system libraries:

```bash
sudo apt-get install -y libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
  libatspi2.0-0 libxdamage1 libxkbcommon0 libxcomposite1 libxrandr2 \
  libgbm1 libpango-1.0-0 libcairo2 libasound2t64
```

**`mcp_client.py` cannot connect.** The server and client must use the same
port. The client defaults to `http://127.0.0.1:8080/mcp`; if the server runs
elsewhere, set `PORT` on the server and `MCP_SERVER_URL` on the client to match.
In PowerShell set variables on their own line before `python`.

**`Session terminated` (not `ConnectError`).** Something is answering on the port
that is not the MCP server — usually the full app, which requires a key. Start
the standalone server (`python -m app.mcp_server`) or send the key
(`MCP_API_KEY`).

**401 on every request.** `Authorization: Bearer <key>` is missing, or the key
does not match `API_KEYS`. Set `REQUIRE_AUTH=false` for local experiments only.

## Project layout

```
app/
  config.py      settings (.env)
  errors.py      one JSON error shape
  auth.py        hashed API keys (SQLite), principals, FastAPI dependencies
  middleware.py  auth + rate limit + security headers + access log (ASGI)
  ratelimit.py   per-key sliding-window limiter
  snapshot.py    in-page JS that builds the ref-addressable snapshot
  browser.py     shared browser + isolated per-key session contexts
  actions.py     19 atomic actions
  tools.py       OpenAI tool specs + dispatcher
  llm.py         minimal OpenAI-compatible async client
  agent.py       the perceive -> act -> observe loop
  sitecheck.py   the website test harness
  main.py        FastAPI app (mounts the MCP server at /mcp)
  mcp_server.py  MCP server (22 tools, per-key sessions)
  routers/       browser, agents, sitecheck, admin
tests/           pytest suite + local fixture site
examples/        demo.py, run_agent.py, mcp_client.py
deploy/          Caddyfile (TLS reverse proxy)
```
