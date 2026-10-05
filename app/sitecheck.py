"""Website test harness.

Loads a URL in the real browser and reports whether it actually works: HTTP
status, page title, required elements and text, console/page errors, failed
requests, same-origin broken links, a few accessibility basics, and a
screenshot. This is the "the LLM built a site — run and test it" path.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.browser import Session
from app.config import get_settings

_IMG_ALT_JS = """() => Array.from(document.querySelectorAll('img'))
  .filter(el => !el.hasAttribute('alt')).length"""

_UNLABELLED_INPUTS_JS = """() => Array.from(document.querySelectorAll('input,select,textarea'))
  .filter(el => {
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'hidden' || type === 'submit' || type === 'button') return false;
    if (el.getAttribute('aria-label') || el.getAttribute('aria-labelledby')) return false;
    if (el.labels && el.labels.length) return false;
    if (el.closest('label') || el.title) return false;
    return true;
  }).length"""

_NAMELESS_BUTTONS_JS = """() => Array.from(document.querySelectorAll('button,[role=button]'))
  .filter(el => !(el.getAttribute('aria-label') || el.innerText || el.getAttribute('title') || '').trim()).length"""


def _check(name: str, passed: bool, detail: str = "") -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


async def _broken_links(session: Session, page_url: str, limit: int) -> list[dict[str, Any]]:
    hrefs = await session.page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    origin = urlparse(page_url).netloc
    candidates: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https") or parsed.netloc != origin:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        candidates.append(absolute)
        if len(candidates) >= limit:
            break

    broken: list[dict[str, Any]] = []
    if not candidates:
        return broken
    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
        for target in candidates:
            try:
                response = await client.head(target)
                if response.status_code >= 400:
                    broken.append({"url": target, "status": response.status_code})
            except httpx.HTTPError as exc:
                broken.append({"url": target, "error": type(exc).__name__})
    return broken


async def run_site_check(
    session: Session,
    url: str,
    *,
    required_selectors: list[str] | None = None,
    required_text: list[str] | None = None,
    check_links: bool = True,
    max_links: int = 25,
    screenshot: bool = True,
) -> dict[str, Any]:
    settings = get_settings()
    page = session.page
    session.reset_recordings()

    checks: list[dict[str, Any]] = []

    started = time.perf_counter()
    response = await page.goto(url, wait_until="domcontentloaded")
    load_ms = round((time.perf_counter() - started) * 1000)
    status = response.status if response else None

    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except PlaywrightTimeoutError:
        pass
    total_ms = round((time.perf_counter() - started) * 1000)

    title = (await page.title()).strip()
    body_text = (await page.inner_text("body"))[: settings.max_text_chars]

    checks.append(_check("http_status_ok", status is not None and status < 400, f"status={status}"))
    checks.append(_check("has_title", bool(title), f"title={title!r}" if title else "no <title>"))

    for selector in required_selectors or []:
        count = await page.locator(selector).count()
        checks.append(_check(f"selector:{selector}", count > 0, f"{count} match(es)"))

    for needle in required_text or []:
        present = needle.lower() in body_text.lower()
        checks.append(_check(f"text:{needle}", present, "found" if present else "not found"))

    console_errors = [c for c in session.console if c["type"] == "error"]
    checks.append(_check("no_console_errors", not console_errors, f"{len(console_errors)} error(s)"))
    checks.append(_check("no_page_errors", not session.page_errors, f"{len(session.page_errors)} uncaught error(s)"))
    checks.append(
        _check("no_failed_requests", not session.failed_requests, f"{len(session.failed_requests)} failed request(s)")
    )

    images_without_alt = await page.evaluate(_IMG_ALT_JS)
    unlabelled_inputs = await page.evaluate(_UNLABELLED_INPUTS_JS)
    nameless_buttons = await page.evaluate(_NAMELESS_BUTTONS_JS)
    checks.append(_check("images_have_alt", images_without_alt == 0, f"{images_without_alt} img without alt"))
    checks.append(_check("inputs_have_labels", unlabelled_inputs == 0, f"{unlabelled_inputs} input without label"))
    checks.append(_check("buttons_have_names", nameless_buttons == 0, f"{nameless_buttons} button without name"))

    broken_links: list[dict[str, Any]] = []
    if check_links:
        broken_links = await _broken_links(session, page.url, max_links)
        checks.append(_check("links_not_broken", not broken_links, f"{len(broken_links)} broken link(s)"))

    screenshot_path: str | None = None
    if screenshot:
        out_dir = Path(settings.artifacts_dir) / "sitecheck"
        out_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = str(out_dir / f"{session.id}-{int(time.time() * 1000)}.png")
        await page.screenshot(path=screenshot_path, full_page=True)

    failed = [c for c in checks if not c["passed"]]
    return {
        "ok": True,
        "url": page.url,
        "requested_url": url,
        "status": status,
        "title": title,
        "passed": not failed,
        "summary": {"total": len(checks), "passed": len(checks) - len(failed), "failed": len(failed)},
        "checks": checks,
        "console_errors": console_errors,
        "page_errors": list(session.page_errors),
        "failed_requests": list(session.failed_requests),
        "http_errors": list(session.http_errors),
        "broken_links": broken_links,
        "timings_ms": {"load": load_ms, "total": total_ms},
        "screenshot": screenshot_path,
    }
