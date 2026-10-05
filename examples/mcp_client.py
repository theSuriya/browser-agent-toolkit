"""Talk to the MCP server the way an LLM host does.

Start the server FIRST in one terminal, then run this client in a second one.

Linux / macOS:
    MCP_TRANSPORT=streamable-http python -m app.mcp_server      # terminal 1
    python examples/mcp_client.py                               # terminal 2

Windows PowerShell (inline VAR=value does NOT work in PowerShell):
    $env:MCP_TRANSPORT = "streamable-http"; python -m app.mcp_server   # terminal 1
    python examples/mcp_client.py                                      # terminal 2

Override ports: PORT on the server, MCP_SERVER_URL on the client.
If the server requires an API key (the full packaged app), set MCP_API_KEY.
"""

import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://127.0.0.1:8080/mcp")


def _payload(result) -> dict:
    """Return the tool result as a dict: structured content, or the JSON text payload."""
    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured
    for item in result.content or []:
        text = getattr(item, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return {}


async def main() -> None:
    headers: dict[str, str] = {}
    api_key = os.environ.get("MCP_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with streamablehttp_client(SERVER_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(f"connected to {SERVER_URL}")
            tools = await session.list_tools()
            print("available tools:", [t.name for t in tools.tools])

            nav = await session.call_tool("browser_navigate", {"url": "https://example.com"})
            print("navigate ->", _payload(nav))

            snapshot = await session.call_tool("browser_snapshot", {})
            print("page title:", _payload(snapshot).get("title"))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001 - turn a stack trace into a clear next step
        detail: BaseException = exc
        if isinstance(exc, BaseExceptionGroup) and exc.exceptions:
            detail = exc.exceptions[0]
        print(
            f"\nCould not complete an MCP session with {SERVER_URL}.\n"
            f"  reason: {type(detail).__name__}: {detail}\n\n"
            "Is the MCP server running in another terminal? Start it first:\n\n"
            "  Linux/macOS:  MCP_TRANSPORT=streamable-http python -m app.mcp_server\n"
            '  PowerShell:   $env:MCP_TRANSPORT = "streamable-http"; python -m app.mcp_server\n\n'
            "If you started the full app (uvicorn app.main:app) instead, it requires an API key:\n"
            '  add -H "Authorization: Bearer <key>" and set API_KEYS before starting it.\n'
            "If port 8080 is busy, run the server with a different PORT and set MCP_SERVER_URL."
        )
        raise SystemExit(1)
