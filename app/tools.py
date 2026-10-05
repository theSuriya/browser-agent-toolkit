"""Tool specifications (OpenAI function-calling format) and the dispatcher.

The same tool set backs three front doors: the LLM agent loop, the REST API and
the MCP server. ``run_tool`` is the single entry point that validates arguments,
serialises actions on a session and normalises errors to a JSON result.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.actions import ACTIONS
from app.browser import Session
from app.errors import AppError

logger = logging.getLogger(__name__)

_TARGET_DOC = "Prefer 'ref' from the latest snapshot. 'selector' (CSS) and 'text' are fallbacks."

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "navigate",
            "description": "Open a URL in the browser and wait for it to load.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Absolute URL to open."},
                    "wait_until": {
                        "type": "string",
                        "enum": ["domcontentloaded", "load", "networkidle", "commit"],
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "snapshot",
            "description": (
                "Return the current page as a compact structure: title, url, visible "
                "interactive elements (each with a stable 'ref'), headings and visible text. "
                "Call this first and after every navigation or click to see what to do next."
            ),
            "parameters": {
                "type": "object",
                "properties": {"include_text": {"type": "boolean", "default": True}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": f"Click an element. {_TARGET_DOC}",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "Element ref, e.g. 'e3'."},
                    "selector": {"type": "string"},
                    "text": {"type": "string"},
                    "timeout_ms": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": f"Type text into an input or textarea. {_TARGET_DOC}",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "ref": {"type": "string"},
                    "selector": {"type": "string"},
                    "clear": {"type": "boolean", "default": True, "description": "Clear the field first."},
                    "submit": {"type": "boolean", "default": False, "description": "Press Enter after typing."},
                    "timeout_ms": {"type": "integer"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": "Press a keyboard key, optionally focused on an element (e.g. 'Enter', 'Escape', 'Tab').",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "ref": {"type": "string"}, "selector": {"type": "string"}},
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_option",
            "description": "Select an option in a <select> element by value or label.",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}, "ref": {"type": "string"}, "selector": {"type": "string"}},
                "required": ["value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hover",
            "description": f"Move the mouse over an element. {_TARGET_DOC}",
            "parameters": {
                "type": "object",
                "properties": {"ref": {"type": "string"}, "selector": {"type": "string"}, "text": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Scroll the page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["down", "up", "left", "right"], "default": "down"},
                    "amount": {"type": "integer", "default": 800},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait_for",
            "description": "Wait until a selector or text appears on the page.",
            "parameters": {
                "type": "object",
                "properties": {"selector": {"type": "string"}, "text": {"type": "string"}, "timeout_ms": {"type": "integer"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_text",
            "description": "Return the visible text of the page (or of a selector).",
            "parameters": {"type": "object", "properties": {"selector": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_html",
            "description": "Return the HTML of the page (or of a selector).",
            "parameters": {"type": "object", "properties": {"selector": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_attribute",
            "description": "Read an attribute (e.g. 'href', 'value') of an element.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "ref": {"type": "string"}, "selector": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "eval_js",
            "description": "Evaluate a JavaScript expression in the page and return its JSON result.",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_links",
            "description": "Return all hyperlink targets on the page.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_console",
            "description": "Return console messages, page errors, failed requests and 4xx/5xx responses seen so far.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": "Capture a PNG screenshot of the page and return its file path.",
            "parameters": {"type": "object", "properties": {"full_page": {"type": "boolean", "default": False}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "go_back",
            "description": "Go back in browser history.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "go_forward",
            "description": "Go forward in browser history.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reload",
            "description": "Reload the current page.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

TOOL_NAMES = {spec["function"]["name"] for spec in TOOL_SPECS}


def _filter_kwargs(func, args: dict[str, Any]) -> dict[str, Any]:
    params = inspect.signature(func).parameters
    allowed = {name for name in params if name != "session"}
    return {k: v for k, v in args.items() if k in allowed and v is not None}


async def run_tool(session: Session, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a named tool against a session and never leak a stack trace."""
    args = args or {}
    action = ACTIONS.get(name)
    if action is None:
        raise AppError(400, "unknown_tool", f"Unknown tool '{name}'. Available: {sorted(ACTIONS)}")

    required = [
        p.name
        for p in inspect.signature(action).parameters.values()
        if p.name != "session" and p.default is inspect.Parameter.empty
    ]
    missing = [r for r in required if args.get(r) in (None, "")]
    if missing:
        raise AppError(400, "missing_arguments", f"Missing required argument(s): {missing}")

    kwargs = _filter_kwargs(action, args)
    try:
        async with session.lock:
            return await action(session, **kwargs)
    except AppError:
        raise
    except PlaywrightTimeoutError as exc:
        return {"ok": False, "error": "timeout", "message": str(exc).splitlines()[0]}
    except PlaywrightError as exc:
        return {"ok": False, "error": "browser_error", "message": str(exc).splitlines()[0]}
    except Exception as exc:  # noqa: BLE001 - tool boundary: return to the caller, log the detail
        logger.exception("Tool %s failed", name)
        return {"ok": False, "error": "internal_error", "message": type(exc).__name__}
