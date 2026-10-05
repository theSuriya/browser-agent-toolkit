"""Talk to the MCP server the way an LLM host does.

Start the server first, then run this client:

    MCP_TRANSPORT=streamable-http python -m app.mcp_server   # serves http://127.0.0.1:8080/mcp
    python examples/mcp_client.py
"""

import asyncio
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://127.0.0.1:8080/mcp")


def _payload(result) -> dict:
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    return {"content": [getattr(c, "text", "") for c in (result.content or [])]}


async def main() -> None:
    async with streamablehttp_client(SERVER_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("available tools:", [t.name for t in tools.tools])

            await session.call_tool("browser_navigate", {"url": "https://example.com"})
            snapshot = await session.call_tool("browser_snapshot", {})
            print("page:", _payload(snapshot).get("title"))


if __name__ == "__main__":
    asyncio.run(main())
