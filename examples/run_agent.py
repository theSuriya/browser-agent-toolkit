"""Run a natural-language browser task with your own LLM.

    export LLM_API_KEY=sk-...
    python examples/run_agent.py
"""

import asyncio

from app.agent import run_agent
from app.browser import manager


async def main() -> None:
    session = await manager.create_session()
    try:
        outcome = await run_agent(session, "Go to https://example.com and return the main heading text.")
        print("\nRESULT:", outcome["result"])
        print(f"steps: {outcome['steps']}")
        for step in outcome["transcript"]:
            status = step["result"].get("ok")
            print(f"  {step['tool']}({step['args']}) -> {status}")
    finally:
        await manager.close_session(session.id)
        await manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
