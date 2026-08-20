"""Browser-driven tests for the dashboard (app/static/dashboard.html) --
what the unit/API test suite in tests/ can't cover, since it never renders
JS or lays out the page. Complements, doesn't replace: these check that
the dashboard actually works as a page a user clicks around in, not the
correctness of the tracking/incident logic behind it (that's tests/'s job).
"""

from __future__ import annotations

import time

import requests

DETECTION_BODY = {
    "sensor_id": "radar-1", "sensor_type": "radar",
    "latitude": 51.4970, "longitude": -0.1150, "confidence": 0.9,
}


def _seed_moving_track(base_url: str, steps: int = 6) -> None:
    """Posts a short sequence of detections that form one track with real
    heading/speed and enough history for the Playback scrubber to appear
    (which requires 2+ positioned detections).
    """
    lat, lon = DETECTION_BODY["latitude"], DETECTION_BODY["longitude"]
    for _ in range(steps):
        lat += 0.00012
        lon += 0.00012
        body = {**DETECTION_BODY, "latitude": lat, "longitude": lon}
        r = requests.post(f"{base_url}/api/detections", json=body, timeout=5)
        r.raise_for_status()
        time.sleep(0.05)


def test_dashboard_loads_with_no_console_errors(live_server, page):
    # Real JS errors (an uncaught exception, an app-level console.error) --
    # kept strict. A bare "Failed to load resource: net::..." is the
    # browser reporting a *network* failure (a slow/rate-limited/blocked
    # map-tile CDN), not this page's own code; failing the whole suite on
    # that would make CI flaky for reasons unrelated to whether the
    # dashboard actually works, so those are excluded here specifically
    # (not console errors in general).
    console_errors = []
    page.on(
        "console",
        lambda msg: console_errors.append(msg.text)
        if msg.type == "error" and not msg.text.startswith("Failed to load resource: net::")
        else None,
    )
    page.on("pageerror", lambda exc: console_errors.append(f"pageerror: {exc}"))

    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector("#map")
    page.wait_for_selector("#tracks-table")

    assert console_errors == []


def test_tracks_table_reflects_ingested_detections(live_server, page):
    _seed_moving_track(live_server)
    page.goto(live_server, wait_until="networkidle")

    page.wait_for_selector("tbody tr[data-id]")
    rows = page.locator("tbody tr[data-id]")
    assert rows.count() == 1


def test_selecting_a_track_shows_details_and_playback_scrubber(live_server, page):
    _seed_moving_track(live_server)
    page.goto(live_server, wait_until="networkidle")

    page.wait_for_selector("tbody tr[data-id]")
    page.click("tbody tr[data-id]")

    page.wait_for_selector("#focus-btn")
    assert page.locator(".detail-header .title").inner_text() != ""

    # Playback only renders once the track's history (2+ points) has
    # loaded -- give the async fetch a moment.
    page.wait_for_selector("#scrub-range", timeout=5000)
    scrub = page.locator("#scrub-range")
    live_btn = page.locator("#live-btn")
    assert live_btn.is_disabled()  # starts in "live" mode, not scrubbing

    box = scrub.bounding_box()
    page.mouse.move(box["x"] + 5, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.3, box["y"] + box["height"] / 2)
    page.mouse.up()

    assert not live_btn.is_disabled()  # now scrubbing history
    assert page.locator("#scrub-time").inner_text() != "Live"


def test_map_layer_toggles_are_clickable(live_server, page):
    """Regression test: Leaflet's default zoom control used to sit exactly
    on top of #map-layer-toggles (both top-left), silently intercepting
    clicks meant for the Trails/Vectors/Uncertainty checkboxes underneath
    it. If that regresses, these clicks time out instead of toggling.
    """
    _seed_moving_track(live_server)
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector("#toggle-trail")

    for checkbox_id in ("#toggle-trail", "#toggle-vectors", "#toggle-uncertainty"):
        checkbox = page.locator(checkbox_id)
        was_checked = checkbox.is_checked()
        checkbox.click(timeout=3000)
        assert checkbox.is_checked() != was_checked


def test_search_filters_the_tracks_table(live_server, page):
    _seed_moving_track(live_server)
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector("tbody tr[data-id]")

    page.fill("#search-input", "nonexistent-uid-search-term")
    page.wait_for_selector(".empty")
    assert page.locator("tbody tr[data-id]").count() == 0

    page.fill("#search-input", "")
    page.wait_for_selector("tbody tr[data-id]")
    assert page.locator("tbody tr[data-id]").count() == 1
