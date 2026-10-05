"""Atomic browser actions executed against a session's page.

Every action takes the ``Session`` first and returns a JSON-serialisable dict.
Errors are raised as Playwright/AppError exceptions and normalised to a single
``{"ok": false, "error": ...}`` shape by the dispatcher in ``app.tools``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Coroutine

from playwright.async_api import Locator, Page

from app.browser import Session
from app.config import get_settings
from app.errors import AppError
from app.snapshot import SNAPSHOT_JS, truncate

SCROLL_DELTAS: dict[str, tuple[int, int]] = {
    "down": (0, 800),
    "up": (0, -800),
    "right": (800, 0),
    "left": (-800, 0),
}


def _locator(page: Page, ref: str | None, selector: str | None, text: str | None) -> Locator:
    """Resolve a target to a single locator, preferring a stable agent ref."""
    if ref:
        return page.locator(f'[data-agent-ref="{ref}"]')
    if selector:
        return page.locator(selector)
    if text:
        return page.get_by_text(text).first
    raise AppError(400, "no_target", "Provide one of: ref, selector or text.")


async def navigate(session: Session, url: str, wait_until: str = "domcontentloaded") -> dict[str, Any]:
    response = await session.page.goto(url, wait_until=wait_until)
    return {
        "ok": True,
        "url": session.page.url,
        "title": await session.page.title(),
        "status": response.status if response else None,
    }


async def snapshot(session: Session, include_text: bool = True) -> dict[str, Any]:
    data = await session.page.evaluate(SNAPSHOT_JS)
    if include_text:
        data["text"] = truncate(data.get("text", ""), get_settings().max_text_chars)
    else:
        data.pop("text", None)
    return {"ok": True, **data}


async def click(
    session: Session,
    ref: str | None = None,
    selector: str | None = None,
    text: str | None = None,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    target = _locator(session.page, ref, selector, text).first
    await target.click(timeout=timeout_ms)
    return {"ok": True, "url": session.page.url, "title": await session.page.title()}


async def type_text(
    session: Session,
    text: str,
    ref: str | None = None,
    selector: str | None = None,
    clear: bool = True,
    submit: bool = False,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    target = _locator(session.page, ref, selector, None).first
    if clear:
        await target.fill(text, timeout=timeout_ms)
    else:
        await target.click(timeout=timeout_ms)
        await target.type(text, delay=20)
    if submit:
        await target.press("Enter")
    return {"ok": True, "url": session.page.url}


async def press_key(
    session: Session,
    key: str,
    ref: str | None = None,
    selector: str | None = None,
) -> dict[str, Any]:
    if ref or selector:
        await _locator(session.page, ref, selector, None).first.press(key)
    else:
        await session.page.keyboard.press(key)
    return {"ok": True, "url": session.page.url}


async def select_option(
    session: Session,
    value: str,
    ref: str | None = None,
    selector: str | None = None,
) -> dict[str, Any]:
    await _locator(session.page, ref, selector, None).first.select_option(value)
    return {"ok": True, "value": value}


async def hover(
    session: Session,
    ref: str | None = None,
    selector: str | None = None,
    text: str | None = None,
) -> dict[str, Any]:
    await _locator(session.page, ref, selector, text).first.hover()
    return {"ok": True}


async def scroll(session: Session, direction: str = "down", amount: int = 800) -> dict[str, Any]:
    if direction not in SCROLL_DELTAS:
        raise AppError(400, "bad_direction", f"direction must be one of {sorted(SCROLL_DELTAS)}")
    base_x, base_y = SCROLL_DELTAS[direction]
    scale = amount / abs(base_y or base_x or 1)
    await session.page.mouse.wheel(int(base_x * scale), int(base_y * scale))
    position = await session.page.evaluate("() => window.scrollY")
    return {"ok": True, "scrollY": position}


async def wait_for(
    session: Session,
    selector: str | None = None,
    text: str | None = None,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    page = session.page
    if selector:
        await page.wait_for_selector(selector, timeout=timeout_ms)
    elif text:
        await page.get_by_text(text).first.wait_for(timeout=timeout_ms)
    else:
        raise AppError(400, "no_target", "Provide selector or text to wait for.")
    return {"ok": True}


async def get_text(session: Session, selector: str | None = None) -> dict[str, Any]:
    page = session.page
    raw = await page.locator(selector).first.inner_text() if selector else await page.inner_text("body")
    return {"ok": True, "text": truncate(raw, get_settings().max_text_chars)}


async def get_html(session: Session, selector: str | None = None) -> dict[str, Any]:
    page = session.page
    raw = await page.inner_html(selector) if selector else await page.content()
    return {"ok": True, "html": truncate(raw, get_settings().max_html_chars)}


async def get_attribute(
    session: Session,
    name: str,
    ref: str | None = None,
    selector: str | None = None,
) -> dict[str, Any]:
    value = await _locator(session.page, ref, selector, None).first.get_attribute(name)
    return {"ok": True, "name": name, "value": value}


async def eval_js(session: Session, expression: str) -> dict[str, Any]:
    result = await session.page.evaluate(expression)
    return {"ok": True, "result": result}


async def get_links(session: Session) -> dict[str, Any]:
    hrefs = await session.page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    return {"ok": True, "links": sorted({h for h in hrefs if h})}


async def get_console(session: Session) -> dict[str, Any]:
    return {
        "ok": True,
        "console": list(session.console),
        "page_errors": list(session.page_errors),
        "failed_requests": list(session.failed_requests),
        "http_errors": list(session.http_errors),
    }


async def screenshot(session: Session, full_page: bool = False) -> dict[str, Any]:
    out_dir = Path(get_settings().artifacts_dir) / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{session.id}-{int(time.time() * 1000)}.png"
    await session.page.screenshot(path=str(path), full_page=full_page)
    return {"ok": True, "path": str(path)}


async def go_back(session: Session) -> dict[str, Any]:
    await session.page.go_back()
    return {"ok": True, "url": session.page.url}


async def go_forward(session: Session) -> dict[str, Any]:
    await session.page.go_forward()
    return {"ok": True, "url": session.page.url}


async def reload(session: Session) -> dict[str, Any]:
    await session.page.reload()
    return {"ok": True, "url": session.page.url}


ActionFn = Callable[..., Coroutine[Any, Any, dict[str, Any]]]

ACTIONS: dict[str, ActionFn] = {
    "navigate": navigate,
    "snapshot": snapshot,
    "click": click,
    "type_text": type_text,
    "press_key": press_key,
    "select_option": select_option,
    "hover": hover,
    "scroll": scroll,
    "wait_for": wait_for,
    "get_text": get_text,
    "get_html": get_html,
    "get_attribute": get_attribute,
    "eval_js": eval_js,
    "get_links": get_links,
    "get_console": get_console,
    "screenshot": screenshot,
    "go_back": go_back,
    "go_forward": go_forward,
    "reload": reload,
}
