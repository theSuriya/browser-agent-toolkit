"""MCP server: expose the browser tools to any MCP host (Claude Desktop, IDEs...).

A single shared session is reused across tool calls, so the model can navigate,
observe and act in one continuous browser. Run it with:

    python -m app.mcp_server            # stdio (default, for desktop hosts)
    MCP_TRANSPORT=streamable-http python -m app.mcp_server   # HTTP on a port
"""

import logging
import os  # noqa: E402
from typing import Any

from app.agent import run_agent
from app.browser import Session, manager
from app.config import get_settings
from app.sitecheck import run_site_check
from app.tools import run_tool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

try:  # MCP Python SDK v1
    from mcp.server.fastmcp import FastMCP as _Server
except ImportError:  # MCP Python SDK v2 renamed FastMCP -> MCPServer
    from mcp.server import MCPServer as _Server  # type: ignore[attr-defined]

mcp = _Server("browser-agent")

_session: Session | None = None


async def _current_session() -> Session:
    global _session
    if _session is None:
        _session = await manager.create_session()
    return _session


@mcp.tool()
async def browser_navigate(url: str, wait_until: str = "domcontentloaded") -> dict[str, Any]:
    """Open a URL in the shared browser and return the new page url and title."""
    session = await _current_session()
    return await run_tool(session, "navigate", {"url": url, "wait_until": wait_until})


@mcp.tool()
async def browser_snapshot(include_text: bool = True) -> dict[str, Any]:
    """Return the current page as title, url, interactive elements (with refs), headings and text.
    Call this first, and again after any action that changes the page."""
    session = await _current_session()
    return await run_tool(session, "snapshot", {"include_text": include_text})


@mcp.tool()
async def browser_click(ref: str = "", selector: str = "", text: str = "") -> dict[str, Any]:
    """Click an element. Prefer 'ref' from the latest snapshot; 'selector' or 'text' are fallbacks."""
    session = await _current_session()
    return await run_tool(session, "click", {"ref": ref, "selector": selector, "text": text})


@mcp.tool()
async def browser_type(text: str, ref: str = "", selector: str = "", clear: bool = True, submit: bool = False) -> dict[str, Any]:
    """Type text into an input or textarea. Set submit=true to press Enter afterwards."""
    session = await _current_session()
    return await run_tool(
        session, "type_text", {"text": text, "ref": ref, "selector": selector, "clear": clear, "submit": submit}
    )


@mcp.tool()
async def browser_press(key: str, ref: str = "", selector: str = "") -> dict[str, Any]:
    """Press a keyboard key such as 'Enter', 'Escape' or 'Tab'."""
    session = await _current_session()
    return await run_tool(session, "press_key", {"key": key, "ref": ref, "selector": selector})


@mcp.tool()
async def browser_scroll(direction: str = "down", amount: int = 800) -> dict[str, Any]:
    """Scroll the page: direction is down, up, left or right."""
    session = await _current_session()
    return await run_tool(session, "scroll", {"direction": direction, "amount": amount})


@mcp.tool()
async def browser_get_text(selector: str = "") -> dict[str, Any]:
    """Return the visible text of the page, or of a CSS selector on it."""
    session = await _current_session()
    return await run_tool(session, "get_text", {"selector": selector})


@mcp.tool()
async def browser_eval(expression: str) -> dict[str, Any]:
    """Evaluate a JavaScript expression in the page and return its JSON result."""
    session = await _current_session()
    return await run_tool(session, "eval_js", {"expression": expression})


@mcp.tool()
async def browser_screenshot(full_page: bool = False) -> dict[str, Any]:
    """Capture a PNG screenshot and return its file path."""
    session = await _current_session()
    return await run_tool(session, "screenshot", {"full_page": full_page})


@mcp.tool()
async def browser_console() -> dict[str, Any]:
    """Return console messages, uncaught page errors, failed requests and 4xx/5xx responses."""
    session = await _current_session()
    return await run_tool(session, "get_console", {})


@mcp.tool()
async def site_check(
    url: str,
    required_selectors: list[str] | None = None,
    required_text: list[str] | None = None,
) -> dict[str, Any]:
    """Load a URL in a fresh browser and test it: status, title, required elements/text,
    console and page errors, failed requests, same-origin broken links, a11y basics and a
    screenshot. Use this to verify a website you or the user just built."""
    session = await manager.create_session()
    try:
        return await run_site_check(
            session,
            url,
            required_selectors=required_selectors or [],
            required_text=required_text or [],
        )
    finally:
        await manager.close_session(session.id)


@mcp.tool()
async def browser_run_task(task: str, max_steps: int = 25) -> dict[str, Any]:
    """Run a full natural-language browser task with the agent loop, using the shared
    session. Returns the final answer and a step-by-step transcript."""
    session = await _current_session()
    return await run_agent(session, task, max_steps=max_steps)


@mcp.tool()
async def browser_close() -> dict[str, Any]:
    """Close the shared browser session."""
    global _session
    if _session is not None:
        await manager.close_session(_session.id)
        _session = None
    return {"ok": True}


def main() -> None:
    settings = get_settings()
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport in ("streamable-http", "http"):
        # This SDK reads the bind address from mcp.settings; FastMCP.run() takes no host/port.
        mcp.settings.host = settings.host
        mcp.settings.port = settings.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
