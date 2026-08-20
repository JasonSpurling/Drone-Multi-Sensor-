"""Fixtures for browser-driven dashboard tests (see README.md in this
directory). Unlike tests/conftest.py's isolated_db (an in-process
monkeypatch, fine for TestClient's ASGI transport), Playwright needs a real
socket to connect to -- so live_server spawns the actual app as a
subprocess against a fresh temporary SQLite database, waits for it to
report healthy, and tears it down after the test.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

VENDORED_LEAFLET_DIR = Path(__file__).parent / "vendor" / "leaflet"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(base_url: str, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/api/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError) as exc:
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f"App server never became healthy at {base_url}") from last_error


@pytest.fixture
def live_server(tmp_path):
    """Starts the real FastAPI app (via uvicorn, as a subprocess -- not
    TestClient's in-process ASGI transport) against a fresh SQLite database,
    and yields its base URL once /api/health responds.
    """
    port = _free_port()
    db_path = tmp_path / "e2e.db"
    env = {**os.environ, "DRONE_DATABASE_URL": f"sqlite:///{db_path}"}
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(base_url)
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        # Optional local-dev escape hatch: unset in CI (which runs `playwright
        # install chromium` and lets Playwright resolve its own default
        # browser path), useful when a pre-vendored Chromium build at a
        # nonstandard path needs pointing to explicitly.
        executable_path = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH")
        b = p.chromium.launch(headless=True, executable_path=executable_path)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    """A Playwright page with Leaflet's CDN requests (unpkg.com) redirected
    to the vendored copy in tests_e2e/vendor/leaflet/ -- a real third-party
    CDN in CI is exactly the kind of external dependency that makes a test
    suite flaky/slow/rate-limited for no reason; the dashboard's own
    behavior doesn't depend on which copy of Leaflet 1.9.4 it loads.
    """
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    p = ctx.new_page()

    def _route_leaflet(route):
        match = re.search(r"unpkg\.com/leaflet@[\d.]+/dist/(.*)", route.request.url)
        if match:
            local_path = VENDORED_LEAFLET_DIR / match.group(1)
            if local_path.is_file():
                content_type = "text/css" if local_path.suffix == ".css" else (
                    "application/javascript" if local_path.suffix == ".js" else "image/png"
                )
                route.fulfill(status=200, body=local_path.read_bytes(), content_type=content_type)
                return
        route.continue_()

    p.route("**://unpkg.com/**", _route_leaflet)
    yield p
    ctx.close()
