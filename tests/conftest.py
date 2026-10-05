"""Fixtures: a local static site and an isolated browser session per test.

Each test gets its own ``BrowserManager`` so the Playwright connection, the
browser and the event loop all live in the same test function — no state is
shared across tests (the app's global manager would bind to the first test's
loop and hang on the next one).
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

from app.browser import BrowserManager, Session

_SITE_DIR = Path(__file__).parent / "fixtures" / "site"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: ANN002 - silence the test server
        pass


@pytest.fixture(scope="session")
def site_server() -> str:
    handler = functools.partial(_QuietHandler, directory=str(_SITE_DIR))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    yield f"http://{host}:{port}"
    httpd.shutdown()


@pytest.fixture
async def session() -> Session:
    manager = BrowserManager()
    try:
        await manager.start()
    except Exception as exc:  # noqa: BLE001 - no browser installed on this host
        pytest.skip(f"browser unavailable: {exc}")
    created = await manager.create_session()
    try:
        yield created
    finally:
        await manager.close_session(created.id)
        await manager.stop()
