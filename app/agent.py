"""The LLM agent loop: perceive → decide → act → observe → repeat.

The model is given the browser tool set and drives a session until it answers in
plain language or hits the step cap. Each tool call is executed against the real
page and its result is fed back, including errors, so the model can adapt.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from app.actions import snapshot
from app.browser import Session
from app.config import get_settings
from app.llm import LLMClient
from app.tools import TOOL_SPECS, run_tool

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a browser automation agent operating a real Chromium browser \
through tools. You complete the user's task by observing the page and acting on it.

Method:
1. Call snapshot first to see the page (title, url, interactive elements with refs, text).
2. Act using refs from the most recent snapshot: click, type_text, press_key, select_option.
3. Call snapshot again after anything that changes the page (navigation, click, submit).
4. Read data with get_text, get_attribute, get_links or eval_js.
5. When the task is done, stop calling tools and answer in plain language with the result.

Rules:
- Only use refs that appear in the most recent snapshot. Never invent refs or selectors.
- If a tool returns ok:false, re-snapshot and try a different target or approach.
- Keep the final answer concise and specific (facts, numbers, names actually observed).
- Never claim to have done something you did not observe succeed."""


async def initial_observation(session: Session) -> dict[str, Any]:
    state = await snapshot(session, include_text=True)
    return {k: state[k] for k in ("url", "title", "elements", "headings", "text") if k in state}


async def run_agent(
    session: Session,
    task: str,
    *,
    client: LLMClient | None = None,
    max_steps: int | None = None,
    on_step: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    client = client or LLMClient()
    max_steps = max_steps or settings.agent_max_steps

    observation = await initial_observation(session)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
        {
            "role": "user",
            "content": "Current page state:\n" + json.dumps(observation, ensure_ascii=False),
        },
    ]

    transcript: list[dict[str, Any]] = []
    for step in range(1, max_steps + 1):
        message = await client.chat(messages, tools=TOOL_SPECS)
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            return {
                "ok": True,
                "result": (message.get("content") or "").strip(),
                "steps": step,
                "transcript": transcript,
            }

        messages.append(
            {
                "role": "assistant",
                "content": message.get("content"),
                "tool_calls": tool_calls,
            }
        )

        for call in tool_calls:
            name = call.get("function", {}).get("name", "")
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            result = await run_tool(session, name, args)
            entry = {"step": step, "tool": name, "args": args, "result": result}
            transcript.append(entry)
            if on_step is not None:
                await on_step(entry)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": json.dumps(result, ensure_ascii=False)[:8000],
                }
            )

    return {
        "ok": False,
        "error": "max_steps",
        "result": f"Stopped after {max_steps} steps without a final answer.",
        "steps": max_steps,
        "transcript": transcript,
    }
