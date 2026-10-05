"""Watch a real browser being automated, step by step.

Scripted demo (no LLM, runs anywhere):
    python examples/demo.py
    python examples/demo.py --url https://en.wikipedia.org --query "Browser automation"

LLM-driven agent (decides the steps itself — needs a key):
    export LLM_API_KEY=sk-...
    python examples/demo.py --mode agent --task "Search Wikipedia for 'browser automation' and summarize the top result"

Add --headful to actually watch the Chromium window open (needs a display).
Every step writes a screenshot to artifacts/demo/.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Allow `python examples/demo.py` by putting the project root on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SHOT_DIR = Path("artifacts") / "demo"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Browser Agent Toolkit demo")
    parser.add_argument("--mode", choices=["script", "agent"], default="script")
    parser.add_argument("--url", default="https://en.wikipedia.org")
    parser.add_argument("--query", default="Browser automation agent")
    parser.add_argument("--task", default=None, help="Natural-language task for --mode agent")
    parser.add_argument("--max-steps", type=int, default=25)
    parser.add_argument("--headful", action="store_true", help="Show the browser window (needs a display)")
    return parser.parse_args()


def banner(text: str) -> None:
    print("\n" + "-" * 64)
    print(text)
    print("-" * 64)


async def _shot(session, label: str) -> str:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SHOT_DIR / f"{label}.png"
    await session.page.screenshot(path=str(path))
    print(f"   screenshot -> {path}")
    return str(path)


def _find_search_box(elements: list[dict]) -> dict | None:
    for element in elements:
        if element["tag"] == "input" and element.get("type", "").lower() in ("", "text", "search", "q"):
            return element
    return None


async def run_scripted(args: argparse.Namespace) -> None:
    from app.browser import manager
    from app.tools import run_tool

    session = await manager.create_session()
    try:
        banner(f"STEP 1 - open the browser and go to {args.url}")
        nav = await run_tool(session, "navigate", {"url": args.url})
        print(f"   title: {nav['title']!r}  (HTTP {nav['status']})")
        await _shot(session, "1-initial")

        banner("STEP 2 - snapshot: what the agent 'sees' (refs, not CSS selectors)")
        snap = await run_tool(session, "snapshot", {"include_text": False})
        print(f"   {len(snap['elements'])} interactive elements found")
        for element in snap["elements"][:8]:
            print(f"   [{element['ref']:>3}] {element['tag']:<8} {element['name']!r}")
        box = _find_search_box(snap["elements"])
        if box is None:
            print("   (no text input on this page - skipping the typing step)")
            return

        banner(f"STEP 3 - type {args.query!r} into [{box['ref']}] and press Enter")
        await run_tool(session, "type_text", {"ref": box["ref"], "text": args.query, "submit": True})
        await session.page.wait_for_load_state("domcontentloaded")
        print(f"   now at: {session.page.url}")
        await _shot(session, "2-after-search")

        banner("STEP 4 - read the outcome")
        text = await run_tool(session, "get_text", {})
        print("   first lines of the result page:")
        for line in [ln for ln in text["text"].splitlines() if ln.strip()][:8]:
            print(f"     {line}")
        await _shot(session, "3-result")
        banner("DONE - the browser was driven entirely through tools")
    finally:
        await manager.close_session(session.id)
        await manager.stop()


async def run_agent(args: argparse.Namespace) -> None:
    from app.agent import run_agent
    from app.browser import manager

    task = args.task or f"Go to {args.url}, search for '{args.query}', and summarize the top result."
    session = await manager.create_session()
    try:
        banner(f"AGENT TASK: {task}")
        outcome = await run_agent(session, task, max_steps=args.max_steps)
        for step in outcome["transcript"]:
            ok = step["result"].get("ok")
            print(f"   step {step['step']}: {step['tool']}({step['args']}) -> ok={ok}")
        await _shot(session, "agent-final")
        banner("AGENT ANSWER")
        print(f"   {outcome['result']}")
        print(f"   ({outcome['steps']} steps)")
    finally:
        await manager.close_session(session.id)
        await manager.stop()


def main() -> None:
    args = parse_args()
    if args.headful:
        os.environ["HEADLESS"] = "false"  # must be set before the settings are read
    if args.mode == "agent":
        asyncio.run(run_agent(args))
    else:
        asyncio.run(run_scripted(args))


if __name__ == "__main__":
    main()
