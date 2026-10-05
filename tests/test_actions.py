"""Browser action tests against a local static site."""

import pytest

from app.errors import AppError
from app.tools import run_tool


async def test_navigate_and_snapshot(session, site_server):
    result = await run_tool(session, "navigate", {"url": f"{site_server}/good.html"})
    assert result["ok"] is True
    assert result["status"] == 200

    snap = await run_tool(session, "snapshot", {})
    assert snap["ok"] is True
    assert snap["title"] == "Good Site"
    assert snap["elements"], "expected interactive elements with refs"
    assert all(e["ref"].startswith("e") for e in snap["elements"])


async def test_type_and_click_update_the_page(session, site_server):
    await run_tool(session, "navigate", {"url": f"{site_server}/good.html"})
    snap = await run_tool(session, "snapshot", {})

    input_ref = next(e["ref"] for e in snap["elements"] if e["tag"] == "input")
    button_ref = next(e["ref"] for e in snap["elements"] if e["tag"] == "button")

    assert (await run_tool(session, "type_text", {"ref": input_ref, "text": "world"}))["ok"] is True
    assert (await run_tool(session, "click", {"ref": button_ref}))["ok"] is True

    text = await run_tool(session, "get_text", {"selector": "#out"})
    assert "world" in text["text"]


async def test_console_and_links(session, site_server):
    await run_tool(session, "navigate", {"url": f"{site_server}/good.html"})
    links = await run_tool(session, "get_links", {})
    assert any(link.endswith("page2.html") for link in links["links"])

    console = await run_tool(session, "get_console", {})
    assert console["ok"] is True
    assert console["page_errors"] == []


async def test_unknown_tool_raises_app_error(session):
    with pytest.raises(AppError) as exc:
        await run_tool(session, "does_not_exist", {})
    assert exc.value.code == "unknown_tool"


async def test_missing_arguments_raise(session):
    with pytest.raises(AppError) as exc:
        await run_tool(session, "navigate", {})
    assert exc.value.code == "missing_arguments"


async def test_timeout_is_returned_not_raised(session, site_server):
    await run_tool(session, "navigate", {"url": f"{site_server}/good.html"})
    result = await run_tool(session, "click", {"selector": "#does-not-exist", "timeout_ms": 500})
    assert result["ok"] is False
    assert result["error"] in ("timeout", "browser_error")
