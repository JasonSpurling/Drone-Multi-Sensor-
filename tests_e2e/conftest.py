"""Fixtures for browser-driven dashboard tests (see README.md in this
directory). Unlike tests/conftest.py's isolated_db (an in-process
monkeypatch, fine for TestClient's ASGI transport), Playwright needs a real
socket to connect to -- so live_server spawns the actual app as a
subprocess against a fresh temporary SQLite database, waits for it to
report healthy, and tears it down after the test.
"""

from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import Locator, sync_playwright

VISUAL_BASELINE_DIR = Path(__file__).parent / "visual_baselines"
VISUAL_FAILURE_DIR = Path(__file__).parent / ".visual_failures"


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


def _spawn_live_server(tmp_path, extra_env: dict | None = None):
    port = _free_port()
    db_path = tmp_path / "e2e.db"
    env = {**os.environ, "DRONE_DATABASE_URL": f"sqlite:///{db_path}", **(extra_env or {})}
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


@pytest.fixture
def live_server(tmp_path):
    """Starts the real FastAPI app (via uvicorn, as a subprocess -- not
    TestClient's in-process ASGI transport) against a fresh SQLite database,
    and yields its base URL once /api/health responds.
    """
    yield from _spawn_live_server(tmp_path)


@pytest.fixture
def live_server_no_seed_zones(tmp_path):
    """Same as live_server, but with an empty zones seed file -- the
    default live_server (like any deployment that hasn't overridden
    DRONE_ZONES_SEED_PATH) always loads app/zones.seed.json's bundled
    "Central London Restricted Zone" at startup, so the zones panel's
    genuinely-zero-zones empty state is otherwise unreachable in a test.
    """
    seed_path = tmp_path / "empty_zones.json"
    seed_path.write_text("[]")
    yield from _spawn_live_server(tmp_path, {"DRONE_ZONES_SEED_PATH": str(seed_path)})


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
    """A plain Playwright page against the dashboard -- no CDN routing
    needed here since app/static/dashboard.html loads Leaflet from
    app/static/vendor/leaflet/ (served by the live_server subprocess
    itself), not a third-party CDN. See that vendored copy's own README
    note for how to update it when the pinned Leaflet version changes.
    """
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    p = ctx.new_page()
    yield p
    ctx.close()


# Fraction of pixels allowed to differ (beyond _PER_PIXEL_TOLERANCE) before a
# visual regression test fails. Not 0 -- font hinting/anti-aliasing varies by
# a pixel or two at element edges even between two runs on the same machine
# with nothing actually changed, and this suite needs to catch real layout
# regressions (like the rail-badge-positioning bug that motivated it), not
# flag that noise.
_DIFF_RATIO_TOLERANCE = 0.005
# Per-channel (0-255) difference below which a pixel doesn't count as
# "different" at all -- the same anti-aliasing noise, filtered before the
# ratio above ever sees it. 40, not 24: a real CI run (a freshly
# `playwright install`ed Chromium, a different minor version than
# whatever's pinned in a given dev/CI environment at baseline-generation
# time) showed up to ~0.5% of #icon-rail's pixels differing from a
# baseline that was otherwise pixel-identical in content -- soft-edge
# font-hinting/anti-aliasing noise from the version difference, not a
# layout regression. That noise is characteristically small per-pixel
# deltas spread over many pixels; a real regression (like the
# rail-badge-positioning bug this suite exists to catch) is a block of
# fully-wrong-color pixels with a much larger per-pixel delta, so raising
# this floor filters the former without blinding the ratio check to the
# latter.
_PER_PIXEL_TOLERANCE = 40
# Pixels of width/height drift tolerated before a size difference hard-fails
# outright (bypassing the ratio check below entirely). A content-driven
# `width: fit-content`-style element's rendered size can legitimately shift
# by a pixel or two between CI runs of identical content purely from
# sub-pixel font-metric rounding (observed: 455px -> 456px across two CI
# runs with no diff in between) -- the same class of noise
# _PER_PIXEL_TOLERANCE already exists to absorb, just manifesting as a
# dimension change instead of a color change. A real layout regression
# (a misplaced badge, an added/removed button) moves things by much more
# than this.
_SIZE_TOLERANCE_PX = 3


def _assert_matches_visual_baseline(locator: Locator, name: str) -> None:
    """Screenshots `locator` and compares it against
    tests_e2e/visual_baselines/{name}.png, the way a human reviewing a
    screenshot would have caught the rail-badge-positioning bug (see
    app/static/dashboard.html's git history) well before it shipped.

    A missing baseline fails the test rather than silently creating one --
    a new baseline is a real assertion about what "correct" looks like and
    needs a human to look at it once, the same way a new golden file would
    in any snapshot-testing setup. Set UPDATE_VISUAL_BASELINES=1 to write
    (or overwrite) the baseline instead of asserting against it, then
    review the PNG with `git diff`/an image viewer before committing it.
    """
    from PIL import Image, ImageChops

    locator.wait_for(state="visible")
    png_bytes = locator.screenshot(animations="disabled")
    baseline_path = VISUAL_BASELINE_DIR / f"{name}.png"

    if os.environ.get("UPDATE_VISUAL_BASELINES") == "1":
        VISUAL_BASELINE_DIR.mkdir(parents=True, exist_ok=True)
        baseline_path.write_bytes(png_bytes)
        pytest.skip(f"Wrote new baseline for {name!r} -- review it, then re-run without UPDATE_VISUAL_BASELINES")

    if not baseline_path.is_file():
        pytest.fail(
            f"No visual baseline at {baseline_path} -- run with "
            f"UPDATE_VISUAL_BASELINES=1 to create one, review the PNG, and commit it."
        )

    actual = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    expected = Image.open(baseline_path).convert("RGB")

    if actual.size != expected.size:
        width_diff = abs(actual.size[0] - expected.size[0])
        height_diff = abs(actual.size[1] - expected.size[1])
        if width_diff > _SIZE_TOLERANCE_PX or height_diff > _SIZE_TOLERANCE_PX:
            pytest.fail(
                f"{name}: size changed ({expected.size} -> {actual.size}) -- "
                f"review and, if intentional, regenerate with UPDATE_VISUAL_BASELINES=1"
            )
        # Within tolerance -- pad both onto a shared canvas (not resize,
        # which would blur/distort every pixel) so the pixel-ratio diff
        # below still has two same-size images to compare.
        canvas_size = (max(actual.size[0], expected.size[0]), max(actual.size[1], expected.size[1]))

        def _pad(img: Image.Image) -> Image.Image:
            canvas = Image.new("RGB", canvas_size, (255, 255, 255))
            canvas.paste(img, (0, 0))
            return canvas

        actual = _pad(actual)
        expected = _pad(expected)

    diff = ImageChops.difference(actual, expected)
    # A pixel counts as "different" if its worst-case channel delta exceeds
    # the noise floor -- take the per-pixel max across R/G/B.
    r, g, b = diff.split()
    worst = ImageChops.lighter(ImageChops.lighter(r, g), b)
    # histogram() is a 256-bucket count of an "L"-mode image's pixel
    # values -- summing buckets above the tolerance avoids iterating every
    # pixel in Python (and the getdata() API this used to use is
    # deprecated as of Pillow 12).
    differing_pixels = sum(worst.histogram()[_PER_PIXEL_TOLERANCE + 1 :])
    total_pixels = actual.size[0] * actual.size[1]
    diff_ratio = differing_pixels / total_pixels if total_pixels else 0.0

    if diff_ratio > _DIFF_RATIO_TOLERANCE:
        VISUAL_FAILURE_DIR.mkdir(parents=True, exist_ok=True)
        actual_path = VISUAL_FAILURE_DIR / f"{name}.actual.png"
        diff_path = VISUAL_FAILURE_DIR / f"{name}.diff.png"
        actual.save(actual_path)
        diff.save(diff_path)
        pytest.fail(
            f"{name}: {diff_ratio:.2%} of pixels differ from the baseline "
            f"(tolerance {_DIFF_RATIO_TOLERANCE:.2%}). Actual/diff saved to "
            f"{actual_path} / {diff_path} for review. If this is an intentional "
            f"UI change, regenerate the baseline with UPDATE_VISUAL_BASELINES=1."
        )


@pytest.fixture
def assert_visual_baseline():
    """A test-callable fixture (rather than a plain importable function --
    tests_e2e/ isn't a package) wrapping _assert_matches_visual_baseline.
    Usage: assert_visual_baseline(page.locator("#icon-rail"), "icon_rail").
    """
    return _assert_matches_visual_baseline
