"""MCP server: expose the browser tools to any MCP host (Claude Desktop, IDEs...).

Two modes:
  * stdio (default): ``python -m app.mcp_server`` for a local desktop host.
  * HTTP: mounted at ``/mcp`` inside the FastAPI app (see app/main.py), where the
    API-key middleware in front of the whole app also protects it.

Sessions are keyed by the caller's API key, so two callers never share a browser.
"""

import logging
import os
from typing import Any

from app.agent import run_agent
from app.auth import ANONYMOUS, get_current_principal
from app.browser import Session, manager
from app.config import get_settings
from app.errors import AppError
from app.sitecheck import run_site_check
from app.tools import run_tool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("app.mcp")

try:  # MCP Python SDK v1
    from mcp.server.fastmcp import FastMCP as _Server
except ImportError:  # MCP Python SDK v2 renamed FastMCP -> MCPServer
    from mcp.server import MCPServer as _Server  # type: ignore[attr-defined]

_sessions: dict[str, Session] = {}


def _owner() -> str:
    principal = get_current_principal()
    return principal.key_id if principal is not None else ANONYMOUS.key_id


async def _current_session() -> Session:
    owner = _owner()
    principal = get_current_principal()
    max_per_owner = principal.max_sessions_per_key if principal is not None else None
    session = _sessions.get(owner)
    if session is not None:
        try:
            return manager.get(session.id, owner=owner)
        except AppError:
            _sessions.pop(owner, None)
    session = await manager.create_session(owner=owner, max_per_owner=max_per_owner)
    _sessions[owner] = session
    return session


def _args(**kwargs: Any) -> dict[str, Any]:
    return {k: v for k, v in kwargs.items() if v is not None}


def build_mcp() -> "_Server":
    """Create a fresh MCP server with the browser tools registered."""
    server = _Server("browser-agent")

    @server.tool()
    async def browser_navigate(url: str, wait_until: str = "domcontentloaded") -> dict:
        """Open a URL in the browser and return the page title and HTTP status."""
        return await run_tool(await _current_session(), "navigate", {"url": url, "wait_until": wait_until})

    @server.tool()
    async def browser_snapshot(include_text: bool = True) -> dict:
        """Return the interactive elements of the current page as refs (e1, e2, ...)."""
        return await run_tool(await _current_session(), "snapshot", {"include_text": include_text})

    @server.tool()
    async def browser_click(ref: str | None = None, selector: str | None = None, text: str | None = None) -> dict:
        """Click an element by ref from a snapshot, by its text, or by CSS selector."""
        return await run_tool(await _current_session(), "click", _args(ref=ref, selector=selector, text=text))

    @server.tool()
    async def browser_type(text: str, ref: str | None = None, selector: str | None = None, clear: bool = True, submit: bool = False) -> dict:
        """Type text into an input identified by ref or CSS selector."""
        return await run_tool(await _current_session(), "type_text", _args(text=text, ref=ref, selector=selector, clear=clear, submit=submit))

    @server.tool()
    async def browser_press_key(key: str, ref: str | None = None, selector: str | None = None) -> dict:
        """Press a keyboard key such as Enter or Escape on the page or an element."""
        return await run_tool(await _current_session(), "press_key", _args(key=key, ref=ref, selector=selector))

    @server.tool()
    async def browser_select_option(value: str, ref: str | None = None, selector: str | None = None) -> dict:
        """Select an option by value in a select element identified by ref or selector."""
        return await run_tool(await _current_session(), "select_option", _args(value=value, ref=ref, selector=selector))

    @server.tool()
    async def browser_hover(ref: str | None = None, selector: str | None = None, text: str | None = None) -> dict:
        """Hover over an element identified by ref, text, or CSS selector."""
        return await run_tool(await _current_session(), "hover", _args(ref=ref, selector=selector, text=text))

    @server.tool()
    async def browser_scroll(direction: str = "down", amount: int = 1) -> dict:
        """Scroll the page up or down by a number of viewport heights."""
        return await run_tool(await _current_session(), "scroll", {"direction": direction, "amount": amount})

    @server.tool()
    async def browser_wait_for(selector: str | None = None, text: str | None = None, timeout_ms: int | None = None) -> dict:
        """Wait until a selector appears or the given text is present."""
        return await run_tool(await _current_session(), "wait_for", _args(selector=selector, text=text, timeout_ms=timeout_ms))

    @server.tool()
    async def browser_get_text(selector: str) -> dict:
        """Return the visible text of the element matching a CSS selector."""
        return await run_tool(await _current_session(), "get_text", {"selector": selector})

    @server.tool()
    async def browser_get_html(selector: str) -> dict:
        """Return the inner HTML of the element matching a CSS selector."""
        return await run_tool(await _current_session(), "get_html", {"selector": selector})

    @server.tool()
    async def browser_get_attribute(name: str, ref: str | None = None, selector: str | None = None) -> dict:
        """Return an attribute value of an element identified by ref or selector."""
        return await run_tool(await _current_session(), "get_attribute", _args(name=name, ref=ref, selector=selector))

    @server.tool()
    async def browser_eval(expression: str) -> dict:
        """Evaluate a JavaScript expression in the page and return its value."""
        return await run_tool(await _current_session(), "eval_js", {"expression": expression})

    @server.tool()
    async def browser_get_links() -> dict:
        """Return all links on the current page with their text and href."""
        return await run_tool(await _current_session(), "get_links", {})

    @server.tool()
    async def browser_get_console() -> dict:
        """Return console messages and uncaught page errors collected so far."""
        return await run_tool(await _current_session(), "get_console", {})

    @server.tool()
    async def browser_screenshot(full_page: bool = True) -> dict:
        """Capture a screenshot of the current page to the artifacts directory."""
        return await run_tool(await _current_session(), "screenshot", {"full_page": full_page})

    @server.tool()
    async def browser_go_back() -> dict:
        """Go back in the browser history."""
        return await run_tool(await _current_session(), "go_back", {})

    @server.tool()
    async def browser_go_forward() -> dict:
        """Go forward in the browser history."""
        return await run_tool(await _current_session(), "go_forward", {})

    @server.tool()
    async def browser_reload() -> dict:
        """Reload the current page."""
        return await run_tool(await _current_session(), "reload", {})

    @server.tool()
    async def browser_close() -> dict:
        """Close this caller's browser session and free its resources."""
        owner = _owner()
        session = _sessions.pop(owner, None)
        if session is not None:
            await manager.close_session(session.id)
        return {"ok": True}

    @server.tool()
    async def site_check(url: str, required_selectors: list[str] | None = None, required_text: list[str] | None = None) -> dict:
        """Load a URL in a real browser and report status, console and page errors,
        failed requests, broken links, accessibility basics and a screenshot.
        Use this to test a site an LLM just built."""
        owner = _owner()
        session = await manager.create_session(owner=owner)
        try:
            return await run_site_check(
                session, url,
                required_selectors=required_selectors or [],
                required_text=required_text or [],
            )
        finally:
            await manager.close_session(session.id)

    @server.tool()
    async def browser_run_task(task: str, max_steps: int = 25) -> dict:
        """Run a natural-language task in the browser using the configured LLM."""
        session = await _current_session()
        return await run_agent(session, task, max_steps=max_steps)

    return server


mcp = build_mcp()


def main() -> None:
    settings = get_settings()
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport in ("streamable-http", "http"):
        mcp.settings.host = settings.host
        mcp.settings.port = settings.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
