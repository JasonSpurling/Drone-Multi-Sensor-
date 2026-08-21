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
    page.wait_for_selector("#tracks-list")

    assert console_errors == []


def test_tracks_table_reflects_ingested_detections(live_server, page):
    _seed_moving_track(live_server)
    page.goto(live_server, wait_until="networkidle")

    page.wait_for_selector(".track-card[data-id]")
    rows = page.locator(".track-card[data-id]")
    assert rows.count() == 1


def test_selecting_a_track_shows_details_and_playback_scrubber(live_server, page):
    _seed_moving_track(live_server)
    page.goto(live_server, wait_until="networkidle")

    page.wait_for_selector(".track-card[data-id]")
    page.click(".track-card[data-id]")

    page.wait_for_selector("#focus-btn")
    assert page.locator(".detail-title-row").inner_text() != ""

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
    page.wait_for_selector(".track-card[data-id]")

    page.fill("#search-input", "nonexistent-uid-search-term")
    page.wait_for_selector(".empty")
    assert page.locator(".track-card[data-id]").count() == 0

    page.fill("#search-input", "")
    page.wait_for_selector(".track-card[data-id]")
    assert page.locator(".track-card[data-id]").count() == 1


def test_narrow_viewport_details_panel_back_button_is_not_clipped_under_the_rail(live_server, page):
    """Regression test: #details-panel used to be right-anchored at a
    fixed 360px width regardless of viewport, so on a narrow (phone-width)
    screen its left edge landed under the icon rail (which sits on top,
    z-index-wise) -- silently clipping its own back button out of reach.
    The max-width: 760px layout must keep it fully inside the viewport.
    """
    _seed_moving_track(live_server)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(live_server, wait_until="networkidle")

    page.wait_for_selector(".track-card[data-id]")
    page.click(".track-card[data-id]")
    page.wait_for_selector("#details-back")

    back_button = page.locator("#details-back")
    box = back_button.bounding_box()
    assert box is not None
    assert box["x"] >= 0  # not pushed off-screen or under the rail's left edge

    # The rail must stay clickable (it sits on top, but with the panel's
    # back button now inside the viewport, the two shouldn't overlap).
    rail_box = page.locator(".rail-btn[data-panel=tracks]").bounding_box()
    assert rail_box is not None
    assert box["x"] >= rail_box["x"] + rail_box["width"]


def test_narrow_viewport_collapsing_the_left_panel_reveals_the_map(live_server, page):
    _seed_moving_track(live_server)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")

    left_panel = page.locator("#left-panel")
    assert "collapsed" not in (left_panel.get_attribute("class") or "")

    # The tracks icon is already active on load -- clicking it again
    # collapses the panel instead of re-showing it.
    page.click(".rail-btn[data-panel=tracks]")
    page.wait_for_timeout(300)  # CSS transform transition
    assert "collapsed" in (left_panel.get_attribute("class") or "")


def test_track_card_is_keyboard_operable(live_server, page):
    """Regression test: .track-card is a <div> with a click handler, not a
    native <button> (see dashboard.html's makeKeyboardActivatable comment
    for why it isn't one) -- without role="button"/tabindex/a keydown
    handler it would be completely unreachable for a keyboard-only user,
    not just unlabeled for a screen reader.
    """
    _seed_moving_track(live_server)
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")

    card = page.locator(".track-card[data-id]").first
    assert card.get_attribute("role") == "button"
    assert card.get_attribute("tabindex") == "0"

    card.focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("#details-panel[style*='flex']", timeout=3000)
    assert page.locator(".detail-title-row").inner_text() != ""


def test_icon_only_buttons_have_accessible_names(live_server, page):
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector("#tracks-list")

    for selector in (
        '.rail-btn[data-panel="tracks"]',
        '.rail-btn[data-panel="alerts"]',
        '.rail-btn[data-panel="sensors"]',
    ):
        label = page.locator(selector).get_attribute("aria-label")
        assert label, f"{selector} has no accessible name"

    # The details panel's back/close buttons only exist once a track is
    # selected -- seed one and open it.
    requests.post(live_server + "/api/detections", json=DETECTION_BODY, timeout=5)
    page.reload(wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")
    page.click(".track-card[data-id]")
    page.wait_for_selector("#details-back")

    assert page.locator("#details-back").get_attribute("aria-label")
    assert page.locator("#details-close").get_attribute("aria-label")
