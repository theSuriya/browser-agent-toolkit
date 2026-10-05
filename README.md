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
uvicorn app.main:app --port 8080        # REST API + docs at /docs
python -m app.mcp_server                # MCP over stdio (Claude Desktop, IDEs)
MCP_TRANSPORT=streamable-http python -m app.mcp_server   # MCP over HTTP at /mcp
```

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

15+ tests run a real Chromium against a local fixture site, including the agent
loop driven by a scripted fake LLM, so no API key is needed.

## Layout

```
app/
  config.py      settings (.env)
  errors.py      one JSON error shape
  snapshot.py    in-page JS that builds the ref-addressable snapshot
  browser.py     shared browser + isolated per-session contexts
  actions.py     18 atomic actions
  tools.py       OpenAI tool specs + dispatcher
  llm.py         minimal OpenAI-compatible async client
  agent.py       the perceive→act→observe loop
  sitecheck.py   the website test harness
  main.py        FastAPI app
  mcp_server.py  MCP server
  routers/       browser, agents, sitecheck
tests/           pytest suite + local fixture site
examples/        run_agent.py, mcp_client.py
```
