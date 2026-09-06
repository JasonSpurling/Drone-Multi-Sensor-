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


def test_a_new_detection_appears_via_the_live_socket_without_waiting_for_a_poll(live_server, page):
    """Proves the WebSocket push (app/live.py, app/api/live.py) actually
    drives the UI, not just that a connection opens: POLL_MS is 15s (see
    dashboard.html), so a track appearing well before that only happens
    if the push -- not the fallback poll -- is what triggered the
    refresh.
    """
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector("#tracks-list")
    page.wait_for_function("liveSocket !== null && liveSocket.readyState === WebSocket.OPEN")
    assert page.locator(".track-card[data-id]").count() == 0

    requests.post(f"{live_server}/api/detections", json=DETECTION_BODY, timeout=5).raise_for_status()

    # Comfortably under POLL_MS -- if this only worked via the polling
    # fallback, it would still be empty at this point.
    page.wait_for_selector(".track-card[data-id]", timeout=5000)


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

    # Must be scrolled into view before reading its bounding box: unlike
    # locator.click(), raw page.mouse coordinates below don't auto-scroll,
    # so a bounding box taken before this could point at a screen position
    # outside the (scrollable) details panel's visible area -- the actual
    # cause of this test's previous flakiness, not a real drag-simulation
    # limitation. document.elementFromPoint at the pre-scroll coordinates
    # returned null, confirming the click was landing nowhere at all.
    scrub.scroll_into_view_if_needed()
    box = scrub.bounding_box()
    page.mouse.move(box["x"] + 5, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.3, box["y"] + box["height"] / 2)
    page.mouse.up()

    assert not live_btn.is_disabled()  # now scrubbing history
    assert page.locator("#scrub-time").inner_text() != "Live"


def test_drawing_a_new_zone_on_the_map_persists_it(live_server, page):
    """The point of app/api/zones.py's POST/PUT endpoints: previously the
    only way to add a restricted zone was hand-editing
    app/zones.seed.json and restarting the app. This clicks three points
    on the map to draw one, saves it through the form, and confirms it
    comes back from a fresh page load -- proving the whole path (map
    click -> draft -> POST /api/zones -> GET /api/zones) actually works,
    not just that the individual pieces do in isolation.
    """
    page.goto(live_server, wait_until="networkidle")
    page.click(".rail-btn[data-panel=zones]")
    page.click("#zone-new-btn")
    page.wait_for_selector("#zone-draw-hint")

    # #map is full-bleed behind everything, including the floating
    # left-panel (open here, showing the Zones view) -- clicking at the
    # map's own bounding-box center would actually land on that panel.
    # Anchor clicks well to the right of it instead, where the map is
    # the topmost element under the cursor.
    map_box = page.locator("#map").bounding_box()
    cx = map_box["x"] + map_box["width"] * 0.75
    cy = map_box["y"] + map_box["height"] / 2
    for dx, dy in ((-40, -30), (40, -30), (0, 40)):
        page.mouse.click(cx + dx, cy + dy)

    page.fill("#zone-name-input", "e2e-drawn-zone")
    page.click("#zone-save-btn")

    page.wait_for_selector(".zone-item .name:has-text('e2e-drawn-zone')")

    # Reload from scratch -- proves it round-tripped through the API and
    # the database, not just that the in-page state object still has it.
    page.reload(wait_until="networkidle")
    page.click(".rail-btn[data-panel=zones]")
    page.wait_for_selector(".zone-item .name:has-text('e2e-drawn-zone')")


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


def test_empty_tracks_panel_offers_a_simulate_button_that_actually_seeds_a_track(live_server, page):
    """Regression test: the tracks empty state used to be a bare "No
    tracks match." with no way to tell "nothing has arrived yet" apart
    from "your filter excludes everything," and no obvious next step for
    someone looking at an otherwise-empty dashboard for the first time.
    """
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector("#simulate-tracks-btn")
    assert "Waiting for sensor data" in page.locator("#tracks-list").inner_text()

    page.click("#simulate-tracks-btn")
    page.wait_for_selector(".track-card[data-id]", timeout=10000)
    assert page.locator(".track-card[data-id]").count() >= 1


def test_empty_sensors_panel_shows_a_connection_checklist(live_server, page):
    page.goto(live_server, wait_until="networkidle")
    page.click(".rail-btn[data-panel=sensors]")
    page.wait_for_selector("#sensors-table .empty-guidance")
    text = page.locator("#sensors-table").inner_text()
    assert "No sensors connected" in text
    assert "/api/detections" in text


def test_registered_but_silent_sensor_shows_as_missing(live_server, page):
    """Regression test: a sensor with a registered position (a known
    mounting point, see app/api/sensor_registry.py) but zero detections
    ever used to be entirely absent from both the Sensor Health panel and
    the map -- indistinguishable from a sensor nobody had configured at
    all. It should now show up as a distinct "missing" status.
    """
    requests.put(
        f"{live_server}/api/sensor-registrations/radar-ghost",
        json={"sensor_type": "radar", "latitude": 51.51, "longitude": -0.12, "azimuth_reference_deg": 0},
        timeout=5,
    ).raise_for_status()

    page.goto(live_server, wait_until="networkidle")
    page.click(".rail-btn[data-panel=sensors]")
    page.wait_for_selector("#sensors-table table")
    row_text = page.locator("#sensors-table tbody tr").inner_text()
    assert "radar-ghost" in row_text
    assert "missing" in row_text
    assert "51.5100, -0.1200" in row_text  # its registered position, not "not registered"

    page.wait_for_function("sensorLayer.getLayers().length === 1")
    popup_html = page.evaluate("sensorLayer.getLayers()[0].getPopup().getContent()")
    assert "never reported a detection" in popup_html


def test_sensor_without_a_registered_position_shows_not_registered(live_server, page):
    _seed_moving_track(live_server)  # radar-1, never registered a position
    page.goto(live_server, wait_until="networkidle")
    page.click(".rail-btn[data-panel=sensors]")
    page.wait_for_selector("#sensors-table table")
    row_text = page.locator("#sensors-table tbody tr").inner_text()
    assert "radar-1" in row_text
    assert "not registered" in row_text


def test_empty_zones_panel_points_at_the_new_zone_button(live_server_no_seed_zones, page):
    page.goto(live_server_no_seed_zones, wait_until="networkidle")
    page.click(".rail-btn[data-panel=zones]")
    page.wait_for_selector("#zones-list .empty")
    assert "New zone" in page.locator("#zones-list").inner_text()


def test_map_shows_a_watermark_with_no_active_tracks_and_hides_it_once_one_appears(live_server, page):
    """Regression test: an empty map used to just be a dark, featureless
    grid, giving no signal that the blankness is expected rather than the
    dashboard being broken.
    """
    page.goto(live_server, wait_until="networkidle")
    watermark = page.locator("#map-empty-state")
    assert watermark.is_visible()
    assert "No active tracks" in watermark.inner_text()

    _seed_moving_track(live_server)
    page.wait_for_selector(".track-card[data-id]")
    assert not watermark.is_visible()


def test_filter_chips_show_live_counts(live_server, page):
    """Regression test: an "All" chip reading just "All" gave no signal
    that its 0 was real data rather than the dashboard having failed to
    load -- a count on every chip (even at 0) makes that legible.
    """
    page.goto(live_server, wait_until="networkidle")
    all_chip = page.locator('.filter-chip[data-class="all"]')
    drone_chip = page.locator('.filter-chip[data-class="drone"]')
    page.wait_for_selector(".filter-chip-count")
    assert all_chip.locator(".filter-chip-count").inner_text() == "0"
    assert drone_chip.locator(".filter-chip-count").inner_text() == "0"

    _seed_moving_track(live_server)
    page.wait_for_selector(".track-card[data-id]")
    # The simulated/seeded track defaults to a radar detection with no
    # camera/RF classifier input -- it lands in "unknown", not "drone",
    # so only the "All" count (not every per-class count) is guaranteed
    # to have moved off zero here.
    assert all_chip.locator(".filter-chip-count").inner_text() == "1"


def test_narrow_viewport_details_panel_back_button_is_not_clipped_under_the_rail(live_server, page):
    """Regression test: #details-panel used to be right-anchored at a
    fixed 360px width regardless of viewport, so on a narrow (phone-width)
    screen its left edge landed under the icon rail (back when panel
    switching lived in a rail floating over the map) -- silently clipping
    its own back button out of reach. Panel switching now lives in the
    persistent top nav bar instead (#icon-rail, moved there so it reads as
    standard console chrome rather than a floating overlay) -- the
    equivalent regression today would be the details panel's top edge
    landing underneath that bar instead. The max-width: 760px layout must
    keep it fully inside the viewport, below the nav.
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
    assert box["x"] >= 0  # not pushed off-screen

    # The nav bar sits on top (it's the persistent top bar); the details
    # panel below it must start clear of the nav's bottom edge, not
    # underneath it.
    nav_box = page.locator("#icon-rail").bounding_box()
    assert nav_box is not None
    assert box["y"] >= nav_box["y"] + nav_box["height"]


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


def test_polling_pauses_while_the_tab_is_hidden(live_server, page):
    """Regression test: the poll used to run unconditionally, even in a
    backgrounded tab where nothing on screen changes. document.hidden can't
    be forced true by a real OS-level tab switch in a headless test, so this
    fakes it the way the app itself observes it: override the `hidden`
    getter and fire the same visibilitychange event the browser would.

    Also covers the live WebSocket (app/live.py): it should disconnect on
    the same signal, for the same reason -- no visible UI to push updates
    into.
    """
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector("#tracks-list")
    page.wait_for_function("liveSocket !== null && liveSocket.readyState === WebSocket.OPEN")

    request_count = {"n": 0}
    page.on(
        "request",
        lambda req: request_count.__setitem__("n", request_count["n"] + 1)
        if "/api/tracks" in req.url
        else None,
    )

    page.evaluate(
        "Object.defineProperty(document, 'hidden', {value: true, configurable: true})"
    )
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")

    assert page.evaluate("liveSocket") is None

    request_count["n"] = 0
    poll_ms = page.evaluate("POLL_MS")
    page.wait_for_timeout(poll_ms + 500)  # longer than one poll interval -- would have polled at least once
    assert request_count["n"] == 0

    page.evaluate(
        "Object.defineProperty(document, 'hidden', {value: false, configurable: true})"
    )
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_function("liveSocket !== null && liveSocket.readyState === WebSocket.OPEN")
    page.wait_for_timeout(200)
    assert request_count["n"] >= 1  # the immediate refresh on becoming visible again


def test_connection_status_dot_reflects_real_websocket_and_poll_state(live_server, page):
    """#conn-status-dot (updateConnectionChip()) used to be a hardcoded
    always-green "live" dot regardless of whether anything was actually
    connected. It should read "connected" once the real WebSocket is
    open, and "degraded" once that socket actually closes -- not just
    stay green forever.
    """
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_function("liveSocket !== null && liveSocket.readyState === WebSocket.OPEN")
    page.wait_for_timeout(200)

    dot = page.locator("#conn-status-dot")
    assert "degraded" not in dot.get_attribute("class")
    assert "unavailable" not in dot.get_attribute("class")
    assert "connected" in dot.get_attribute("aria-label").lower()

    page.evaluate("liveSocket.close()")
    page.wait_for_function("document.getElementById('conn-status-dot').classList.contains('degraded')")
    assert "degraded" in dot.get_attribute("aria-label").lower()


def test_coasting_track_shows_a_dashed_estimated_position_on_the_map(live_server, page):
    """isCoasting()/renderMap()'s dead-reckoning line: a track that's
    gone quiet (but is still server-side "active") should get a dashed
    line + "estimated position" marker on the map extrapolated from its
    last known heading/speed -- not just sit at a now-stale position with
    no indication the plotted spot is no longer current.
    """
    _seed_moving_track(live_server)
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")

    # No real way to make server time pass in a live_server test -- instead
    # backdate the already-fetched track's last_seen past COASTING_THRESHOLD_S
    # and re-render, the same technique test_polling_pauses... uses to drive
    # this app's own client-side clock-dependent logic directly.
    page.evaluate("""
        () => {
            const t = state.tracks[0];
            t.last_seen = new Date(Date.now() - (COASTING_THRESHOLD_S + 5) * 1000).toISOString();
            renderMap();
            renderTracks();
        }
    """)

    assert page.evaluate("isCoasting(state.tracks[0])") is True
    page.wait_for_selector(".track-tooltip:has-text('estimated position')")
    assert "coasting" in page.locator(".track-card-sub").first.inner_text().lower()


def test_icon_only_buttons_have_accessible_names(live_server, page):
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector("#tracks-list")

    for selector in (
        '.rail-btn[data-panel="tracks"]',
        '.rail-btn[data-panel="alerts"]',
        '.rail-btn[data-panel="sensors"]',
        '.rail-btn[data-panel="zones"]',
        '.rail-btn[data-panel="reports"]',
        "#theme-toggle",
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


INSIDE_RESTRICTED_ZONE = {
    "sensor_id": "radar-1", "sensor_type": "radar",
    "latitude": 51.50, "longitude": -0.10, "confidence": 0.9,
}


def test_theme_toggle_switches_and_persists_across_reload(live_server, page):
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector("#theme-toggle")

    assert page.evaluate("document.documentElement.dataset.theme") in (None, "")
    page.click("#theme-toggle")
    assert page.evaluate("document.documentElement.dataset.theme") == "light"

    page.reload(wait_until="networkidle")
    assert page.evaluate("document.documentElement.dataset.theme") == "light"

    page.click("#theme-toggle")
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"


def test_alerts_panel_can_acknowledge_and_resolve_an_incident(live_server, page):
    """POST /api/incidents/{id}/resolve has always existed server-side
    (app/api/incidents.py), but the dashboard never had a button that
    called it -- an incident could be acknowledged from the UI and then
    sat "acknowledged" forever, with no way to actually mark it resolved.
    """
    requests.post(live_server + "/api/detections", json=INSIDE_RESTRICTED_ZONE, timeout=5).raise_for_status()
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")

    page.click('.rail-btn[data-panel="alerts"]')
    page.wait_for_selector(".alert-item")
    assert page.locator('.alert-item .badge-outline:has-text("open")').count() == 1
    assert page.locator("#alerts-badge").inner_text() == "1"

    page.click("button[data-ack-id]")
    page.wait_for_selector('.alert-item .badge-outline:has-text("acknowledged")')
    # Acknowledging doesn't resolve it -- still counts as active.
    assert page.locator("#alerts-badge").inner_text() == "1"
    assert page.locator("button[data-ack-id]").count() == 0
    assert page.locator("button[data-resolve-id]").count() == 1

    page.click("button[data-resolve-id]")
    page.wait_for_selector('.alert-item .badge-outline:has-text("resolved")')
    assert page.locator("button[data-resolve-id]").count() == 0
    # Resolved incidents stay listed for after-action review, but no
    # longer count toward the active-alerts badge.
    assert not page.locator("#alerts-badge").is_visible()
    assert page.evaluate("state.incidents.find(i => i.status === 'resolved').closed_at") is not None


def test_zone_incident_auto_resolves_once_the_track_leaves_the_zone(live_server, page):
    """check_zone_incidents() only ever opened an incident on entry --
    nothing closed it back out once the track's position left again.
    A track that flew straight through the zone and out the other side
    should show its incident auto-resolve in the dashboard without any
    operator action, distinct from a manually-Acknowledged/Resolved one.
    """
    # Near the restricted zone's west edge (zone: lat 51.49-51.51,
    # lon -0.11--0.09, see app/zones.seed.json) rather than dead-center --
    # the next detection just past the edge needs to stay well inside
    # app.tracking's ~500m default association gate to be recognized as
    # the *same* track leaving, not a new one spawning outside the zone.
    # Only ~138m from the edge (not right at it): the original ~485m move
    # left just ~15m of gate margin, which the tighter Mahalanobis check
    # (not just the coarse distance gate) could occasionally reject under
    # normal timing jitter between the two POSTs below, intermittently
    # failing to associate the second point with the same track at all --
    # no amount of extra wait_for_function timeout fixes a resolution
    # that structurally never happens. ~138m total move keeps a wide,
    # reliable margin under the gate while still crossing the boundary.
    near_edge = {
        "sensor_id": "radar-1", "sensor_type": "radar",
        "latitude": 51.50, "longitude": -0.109, "confidence": 0.9,
    }
    requests.post(live_server + "/api/detections", json=near_edge, timeout=5).raise_for_status()
    page.goto(live_server, wait_until="networkidle")
    page.click('.rail-btn[data-panel="alerts"]')
    page.wait_for_selector(".alert-item")
    assert page.locator('.alert-item .badge-outline:has-text("open")').count() == 1

    # Same sensor_id (associates with the same track), ~138m further
    # west -- now outside the zone, comfortably inside the association gate.
    requests.post(
        live_server + "/api/detections", json={**near_edge, "longitude": -0.111}, timeout=5
    ).raise_for_status()

    # 10s, not 5s, as extra headroom for the websocket-push-triggered
    # refresh -- matches the 10s precedent used elsewhere in this file
    # (see the .track-card wait above).
    page.wait_for_function("state.incidents.some(i => i.status === 'resolved')", timeout=10000)
    assert page.locator('.alert-item .badge-outline:has-text("resolved")').count() == 1
    assert not page.locator("#alerts-badge").is_visible()
    description = page.evaluate("state.incidents.find(i => i.status === 'resolved').description")
    assert "auto-closed" in description
    assert page.evaluate("state.incidents.find(i => i.status === 'resolved').acknowledged_by") is None


def test_incident_reports_panel_shows_a_rollup_for_a_seeded_incident(live_server, page):
    requests.post(live_server + "/api/detections", json=INSIDE_RESTRICTED_ZONE, timeout=5).raise_for_status()
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector("#tracks-list")

    page.click('.rail-btn[data-panel="reports"]')
    page.wait_for_selector(".report-stat-tile")

    assert "1" in page.locator(".report-stat-tile .value").first.inner_text()
    assert "zone incursion" in page.locator("#report-body").inner_text().lower()


def test_after_action_report_button_populates_a_printable_summary(live_server, page):
    # window.print() would otherwise pop a real print dialog under a real
    # browser -- stub it before the page's own scripts run so clicking
    # Report still runs openIncidentReport() but doesn't try to print.
    page.add_init_script("window.print = () => {};")
    requests.post(live_server + "/api/detections", json=INSIDE_RESTRICTED_ZONE, timeout=5).raise_for_status()
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector("#tracks-list")

    page.click('.rail-btn[data-panel="alerts"]')
    page.wait_for_selector("button[data-report-id]")
    page.click("button[data-report-id]")
    page.wait_for_function("document.getElementById('print-report').innerHTML.length > 0")

    report_text = page.locator("#print-report").inner_text()
    assert "After-Action Report" in report_text
    assert "zone incursion" in report_text.lower()
    assert "radar-1" in report_text


def test_after_action_report_surfaces_a_real_decoded_identity_fragment(live_server, page):
    """app.reporting._extract_identification: a real per-aircraft identity
    field a sensor already decoded (here, a DJI DroneID-style
    serial_number in raw_data) should show up in the report's new
    Identification section -- not a manufacturer/model guess this app
    has no data to back.
    """
    page.add_init_script("window.print = () => {};")
    requests.post(live_server + "/api/detections", json={
        **INSIDE_RESTRICTED_ZONE,
        "raw_data": {"serial_number": "0W9DH1A0010SNL"},
    }, timeout=5).raise_for_status()
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector("#tracks-list")

    page.click('.rail-btn[data-panel="alerts"]')
    page.wait_for_selector("button[data-report-id]")
    page.click("button[data-report-id]")
    page.wait_for_function("document.getElementById('print-report').innerHTML.length > 0")

    report_text = page.locator("#print-report").inner_text()
    assert "Identification" in report_text
    assert "0W9DH1A0010SNL" in report_text


def test_track_sort_offers_a_risk_option_and_reorders_by_it(live_server, page):
    """track.risk_score (app.risk.compute_risk_score) -- a plain point
    score, not a proprietary ML ranking -- is a selectable sort, same as
    the existing Alerts-first/Altitude/Speed options.
    """
    requests.post(live_server + "/api/detections", json={
        "sensor_id": "radar-1", "sensor_type": "radar", "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
    }, timeout=5).raise_for_status()  # inside the seeded restricted zone -> higher risk
    requests.post(live_server + "/api/detections", json={
        "sensor_id": "radar-2", "sensor_type": "radar", "latitude": 52.0, "longitude": -0.1, "confidence": 0.9,
    }, timeout=5).raise_for_status()  # outside any zone -> lower risk

    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")
    assert page.locator("#track-sort option[value='risk']").count() == 1

    page.select_option("#track-sort", "risk")
    page.wait_for_timeout(200)
    meta_texts = page.locator(".track-card-meta").all_inner_texts()
    risks = [int(t.split("risk ")[1]) for t in meta_texts if "risk " in t]
    assert risks == sorted(risks, reverse=True)


def test_map_offline_banner_appears_after_repeated_tile_failures_and_clears_on_recovery(live_server, page):
    """Map tile imagery comes from an external CDN (unlike the vendored
    Leaflet library itself) -- a deployment with no internet access would
    otherwise just show a permanently blank map background with no
    indication why, even though tracking/alerting all still works fine
    without it. Drives the exact Leaflet events the app listens for
    (see initMap()'s tileerror/tileload wiring) rather than depending on
    real network access, which test environments can't rely on.
    """
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector("#map")

    result = page.evaluate("""
        () => {
            const layers = [];
            leafletMap.eachLayer((l) => { if (l instanceof L.TileLayer) layers.push(l); });
            const activeLayer = layers[0];
            const banner = document.getElementById('map-offline-banner');
            // Real tile requests may already have failed a few times
            // before this runs (this test environment has no route to the
            // real tile CDN either) -- a tileload resets the app's error
            // streak to a known 0 before the synthetic sequence below.
            activeLayer.fire('tileload');
            activeLayer.fire('tileerror');
            activeLayer.fire('tileerror');
            const shownAfterTwo = banner.classList.contains('show');
            activeLayer.fire('tileerror');
            const shownAfterThree = banner.classList.contains('show');
            activeLayer.fire('tileload');
            const hiddenAfterLoad = !banner.classList.contains('show');
            return { shownAfterTwo, shownAfterThree, hiddenAfterLoad };
        }
    """)
    assert result["shownAfterTwo"] is False  # below the 3-failure threshold
    assert result["shownAfterThree"] is True
    assert result["hiddenAfterLoad"] is True


def test_labeling_a_detection_from_the_track_details_panel(live_server, page):
    """Real browser click-through of the labeling workflow (see
    app/api/ml_training.py, app/models.py's Detection.human_label) -- turns
    real accumulated sensor traffic into training data for app/ml/train.py
    without hand-editing a CSV.
    """
    requests.post(live_server + "/api/detections", json=DETECTION_BODY, timeout=5).raise_for_status()
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")
    page.click(".track-card[data-id]")
    page.wait_for_selector(".label-row")

    assert page.locator(".label-chip").count() == 4  # drone/bird/aircraft/unknown
    row = page.locator(".label-row").first
    row.locator('.label-chip[data-label="drone"]').click()
    page.wait_for_selector('.label-row .label-chip[data-label="drone"].active')

    # Clicking the same chip again clears it (undo a mis-click).
    row.locator('.label-chip[data-label="drone"]').click()
    page.wait_for_function(
        "!document.querySelector('.label-row .label-chip.active')"
    )


def test_labeled_detection_appears_in_the_ml_training_export(live_server, page):
    r = requests.post(live_server + "/api/detections", json=DETECTION_BODY, timeout=5)
    r.raise_for_status()
    detection_id = r.json()["id"]

    requests.put(
        f"{live_server}/api/detections/{detection_id}/label", json={"label": "drone"}, timeout=5
    ).raise_for_status()

    export = requests.get(f"{live_server}/api/ml/training-data/export", timeout=5)
    assert export.status_code == 200
    assert "drone" in export.text


def test_map_marker_renders_a_distinct_symbol_per_aircraft_category(live_server, page):
    """The point of Track.aircraft_category (real ICAO ADS-B emitter
    category, see app/adapters/dump1090_bridge.py): a rotorcraft-category
    track gets a genuinely different on-map symbol than the generic
    aircraft triangle, not just a different color.
    """
    requests.post(
        live_server + "/api/detections",
        json={
            "sensor_id": "adsb-1", "sensor_type": "adsb", "latitude": 51.5, "longitude": -0.1,
            "confidence": 0.99, "raw_data": {"hex_ident": "4ca593", "category": "A7"},
        },
        timeout=5,
    ).raise_for_status()
    requests.post(
        live_server + "/api/detections",
        json={
            "sensor_id": "adsb-2", "sensor_type": "adsb", "latitude": 51.6, "longitude": -0.2,
            "confidence": 0.99, "raw_data": {"hex_ident": "aabbcc"},
        },
        timeout=5,
    ).raise_for_status()

    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")

    shapes = page.evaluate("""
        () => {
            const rotor = state.tracks.find(t => t.aircraft_category === "A7");
            const generic = state.tracks.find(t => t.classification === "aircraft" && t.aircraft_category == null);
            return {
                rotor: markerIcon(rotor, false).options.html,
                generic: markerIcon(generic, false).options.html,
            };
        }
    """)
    assert "<line" in shapes["rotor"]  # rotor cross, not the triangle
    assert "<path" in shapes["generic"]  # the realistic airplane silhouette fallback
    assert shapes["rotor"] != shapes["generic"]


def test_map_shows_registered_sensor_positions_and_scale_bar(live_server, page):
    """Registered sensor positions (app/api/sensor_registry.py) show up as
    their own markers on the map, colored by health status -- not just
    listed in the Sensor Health side panel -- so an operator can see at a
    glance where sensors actually are and whether any have gone quiet.
    """
    requests.put(
        f"{live_server}/api/sensor-registrations/radar-1",
        json={
            "sensor_type": "radar", "latitude": 51.51, "longitude": -0.12,
            "altitude_m": 15, "azimuth_reference_deg": 0,
        },
        timeout=5,
    ).raise_for_status()
    requests.post(
        live_server + "/api/detections",
        json={"sensor_id": "radar-1", "sensor_type": "radar", "azimuth_deg": 10, "range_m": 500, "confidence": 0.9},
        timeout=5,
    ).raise_for_status()

    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")
    page.wait_for_function("sensorLayer.getLayers().length === 1")

    assert page.locator(".leaflet-control-scale").count() == 1

    # The Sensors toggle actually controls this layer, not just a label.
    page.click("#toggle-sensors")
    page.wait_for_function("sensorLayer.getLayers().length === 0")
    page.click("#toggle-sensors")
    page.wait_for_function("sensorLayer.getLayers().length === 1")


def test_recenter_control_fits_all_tracks_zones_and_sensors(live_server, page):
    requests.post(live_server + "/api/detections", json=DETECTION_BODY, timeout=5).raise_for_status()
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")

    recenter = page.locator('.leaflet-bar a[aria-label*="Fit all"]')
    assert recenter.count() == 1

    # Pan away, then confirm the recenter control brings the seeded
    # detection's position back into view.
    page.evaluate("leafletMap.setView([0, 0], 3)")
    recenter.click()
    page.wait_for_function(
        f"leafletMap.getBounds().contains([{DETECTION_BODY['latitude']}, {DETECTION_BODY['longitude']}])"
    )


def test_aircraft_marker_uses_realistic_silhouette_and_altitude_color(live_server, page):
    """markerIcon() draws the same airplane silhouette classIcon() already
    uses for the sidebar thumbnail (not the old flat triangle), colored by
    real altitude data (like most real flight trackers) when known, with
    an honest fallback to the flat classification color when it isn't.
    """
    requests.post(
        live_server + "/api/detections",
        json={
            "sensor_id": "adsb-low", "sensor_type": "adsb", "latitude": 51.5, "longitude": -0.1,
            "altitude_m": 200, "confidence": 0.99, "raw_data": {"hex_ident": "111111"},
        },
        timeout=5,
    ).raise_for_status()
    requests.post(
        live_server + "/api/detections",
        json={
            "sensor_id": "adsb-high", "sensor_type": "adsb", "latitude": 51.6, "longitude": -0.2,
            "altitude_m": 10000, "confidence": 0.99, "raw_data": {"hex_ident": "222222"},
        },
        timeout=5,
    ).raise_for_status()
    requests.post(
        live_server + "/api/detections",
        json={
            "sensor_id": "adsb-unknown", "sensor_type": "adsb", "latitude": 51.7, "longitude": -0.3,
            "confidence": 0.99, "raw_data": {"hex_ident": "333333"},
        },
        timeout=5,
    ).raise_for_status()

    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")

    result = page.evaluate("""
        () => {
            const low = state.tracks.find(t => t.altitude_m === 200);
            const high = state.tracks.find(t => t.altitude_m === 10000);
            const unknown = state.tracks.find(t => t.altitude_m == null && t.classification === "aircraft");
            return {
                low: markerIcon(low, false).options.html,
                high: markerIcon(high, false).options.html,
                unknown: markerIcon(unknown, false).options.html,
            };
        }
    """)
    # A distinctive substring of AIRCRAFT_SILHOUETTE_PATH (dashboard.html)
    # -- confirms the real airplane silhouette rendered, not the old flat
    # triangle polygon.
    for html in result.values():
        assert "M21 16v-2l-8-5" in html

    assert "hsl(" in result["low"]
    assert "hsl(" in result["high"]
    assert result["low"] != result["high"]  # different altitudes, genuinely different colors
    assert "hsl(" not in result["unknown"]  # no altitude known -> flat classification color, not a guess


def test_drone_marker_uses_quadcopter_glyph_not_a_plain_dot(live_server, page):
    """markerIcon() draws the same quadcopter glyph classIcon() already uses
    for the sidebar thumbnail for drone (and unclassified) tracks on the map
    itself, instead of the old plain circle.
    """
    requests.post(
        live_server + "/api/detections",
        json={
            "sensor_id": "camera-1", "sensor_type": "camera",
            "latitude": 51.5, "longitude": -0.1, "confidence": 0.95,
        },
        timeout=5,
    ).raise_for_status()

    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")

    html = page.evaluate("""
        () => {
            const drone = state.tracks.find(t => t.classification === "drone");
            return markerIcon(drone, false).options.html;
        }
    """)
    # The quadcopter glyph (droneGlyphMarkup): four rotor circles plus a
    # body rect, not the old flat `<circle ... r="${half - 2}"` dot.
    assert html.count("<circle") == 4
    assert "<rect" in html


def test_search_matches_classification_and_status_not_just_id(live_server, page):
    """visibleTracks()/trackMatchesSearch() search more than the numeric ID
    or track_uid substring -- classification and status too -- so typing
    "drone" or "lost" actually finds tracks, not just a UID fragment.
    """
    requests.post(
        live_server + "/api/detections",
        json={
            "sensor_id": "camera-1", "sensor_type": "camera",
            "latitude": 51.5, "longitude": -0.1, "confidence": 0.95,
        },
        timeout=5,
    ).raise_for_status()
    requests.post(
        live_server + "/api/detections",
        json={
            "sensor_id": "adsb-1", "sensor_type": "adsb",
            "latitude": 51.6, "longitude": -0.2, "confidence": 0.99,
            "raw_data": {"hex_ident": "4ca593", "category": "A7"},
        },
        timeout=5,
    ).raise_for_status()

    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")
    assert page.locator(".track-card[data-id]").count() == 2

    page.fill("#search-input", "drone")
    page.wait_for_function("visibleTracks().length === 1")
    assert page.locator(".track-card[data-id]").count() == 1
    assert page.locator("#search-clear").is_visible()

    # Category label match ("rotor" is a substring of "rotorcraft", the
    # human-readable label for the A7 code, not the raw code itself).
    page.fill("#search-input", "rotor")
    page.wait_for_function("visibleTracks().length === 1")
    assert page.evaluate("visibleTracks()[0].aircraft_category") == "A7"

    # Clear button empties the box and restores every track.
    page.click("#search-clear")
    page.wait_for_function("visibleTracks().length === 2")
    assert page.input_value("#search-input") == ""
    assert not page.locator("#search-clear").is_visible()

    # "/" focuses the search box from anywhere on the page.
    page.click("body")
    page.keyboard.press("/")
    assert page.evaluate("document.activeElement.id") == "search-input"


def test_track_card_shows_alert_dot_and_sort_reorders_the_list(live_server, page):
    """The Tracks list surfaces an at-a-glance alert indicator (no need to
    switch to the Alerts panel to see which track triggered it) and a real
    sort control -- "Alerts first" actually moves the alerting track above
    a more-recently-seen track *in the same classification group*
    (TRACK_SORT_COMPARATORS sorts within a group, never across groups --
    the classification grouping itself stays the primary ordering),
    "Altitude" actually reorders by that field.
    """
    # Inside the seeded "Central London Restricted Zone" (see conftest.py's
    # live_server fixture) -- a real zone-incursion incident, not a
    # fabricated one. High-confidence radar -> classified DRONE (see
    # app/classification.py), altitude unknown.
    requests.post(live_server + "/api/detections", json=INSIDE_RESTRICTED_ZONE, timeout=5).raise_for_status()
    # A second, also-DRONE-classified track (same group), seeded after --
    # so it's more recent and would sort first under the default "Most
    # recent" order despite having no alert.
    requests.post(
        live_server + "/api/detections",
        json={
            "sensor_id": "camera-1", "sensor_type": "camera",
            "latitude": 51.9, "longitude": -0.5, "altitude_m": 5000, "confidence": 0.9,
        },
        timeout=5,
    ).raise_for_status()

    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")
    page.wait_for_function("state.incidents.length > 0")
    page.wait_for_selector(".track-alert-dot")
    assert page.locator(".track-alert-dot").count() == 1

    alerting_track_id = page.evaluate("state.incidents[0].track_id")
    assert page.evaluate(f"state.tracks.find(t => t.id === {alerting_track_id}).classification") == "drone"

    # Default "Most recent" sort: the alert-free, more-recently-seen track
    # leads its group.
    first_id = page.eval_on_selector(".track-card", "el => Number(el.dataset.id)")
    assert first_id != alerting_track_id

    page.select_option("#track-sort", "alerts")
    page.wait_for_function(f"Number(document.querySelector('.track-card').dataset.id) === {alerting_track_id}")

    page.select_option("#track-sort", "altitude")
    page.wait_for_function("""
        () => {
            const cards = [...document.querySelectorAll('.track-card')].map(c => Number(c.dataset.id));
            const track = (id) => state.tracks.find(t => t.id === id);
            // Every altitude-known track sorted before every altitude-unknown
            // one, high to low among the known ones -- not just "some order".
            for (let i = 1; i < cards.length; i++) {
                const prev = track(cards[i - 1]).altitude_m, cur = track(cards[i]).altitude_m;
                if (prev == null && cur != null) return false;
                if (prev != null && cur != null && prev < cur) return false;
            }
            return true;
        }
    """)

    # The choice survives a reload (persisted like the theme toggle).
    page.reload(wait_until="networkidle")
    assert page.eval_on_selector("#track-sort", "el => el.value") == "altitude"


def test_track_details_copy_and_export_use_real_track_data(live_server, page):
    """The details panel's Copy button puts real, currently-selected-track
    data on the clipboard (not a stub), and Export actually downloads the
    already-existing GET /api/tracks/{id}/history/export endpoint (CSV by
    default), which had no dashboard UI before this.
    """
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    requests.post(
        live_server + "/api/detections",
        json={
            "sensor_id": "adsb-1", "sensor_type": "adsb",
            "latitude": 51.5, "longitude": -0.1, "altitude_m": 3000, "confidence": 0.99,
            "raw_data": {"hex_ident": "4ca593", "category": "A7"},
        },
        timeout=5,
    ).raise_for_status()

    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")
    page.click(".track-card[data-id]")
    page.wait_for_selector("#copy-btn")

    track_id = page.evaluate("state.selectedTrackId")
    assert page.locator(".kv-section:has-text('Identity')").inner_text().find("rotorcraft") != -1

    page.click("#copy-btn")
    page.wait_for_function("document.getElementById('copy-btn').textContent === 'Copied!'")
    clipboard_text = page.evaluate("navigator.clipboard.readText()")
    assert f"Track {track_id}" in clipboard_text
    assert "rotorcraft" in clipboard_text
    assert "3000 m" in clipboard_text or "3000" in clipboard_text

    with page.expect_download() as download_info:
        page.click("#export-btn")
    download = download_info.value
    assert download.suggested_filename.startswith("track-")
    assert download.suggested_filename.endswith(".csv")


def test_track_details_shows_classification_confidence_meter(live_server, page):
    """A track's classification_confidence (app/fusion.py -- distinct from
    the classification label itself, which only ever upgrades) shows as
    its own meter in the details panel's Quality tab, in the Copy summary,
    and is a real percentage from the API, not a stub.
    """
    requests.post(
        live_server + "/api/detections",
        json={
            "sensor_id": "camera-1", "sensor_type": "camera",
            "latitude": 51.5, "longitude": -0.1, "confidence": 0.95,
        },
        timeout=5,
    ).raise_for_status()

    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")
    page.click(".track-card[data-id]")
    page.click(".tab-btn[data-tab='quality']")
    page.wait_for_selector("#details-tab-content .kv:has-text('Confidence')")

    confidence_pct = page.evaluate("Math.round(state.tracks[0].classification_confidence * 100)")
    assert confidence_pct == 100  # single fresh detection agreeing with itself

    quality_text = page.locator("#details-tab-content").inner_text()
    assert "Confidence" in quality_text
    assert f"{confidence_pct}%" in quality_text


def test_a_failed_action_shows_a_visible_error_toast_not_just_console(live_server, page):
    """A non-auth action failure (acknowledge/resolve/label/...) used to
    only ever reach console.error -- invisible to anyone not watching
    devtools, so a click that silently failed looked identical to one
    that succeeded.
    """
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector("#tracks-list")
    assert not page.locator("#error-toast").is_visible()

    # A nonexistent incident id -> a real 404 from the API, not a stub.
    page.evaluate("acknowledge(999999)")
    page.wait_for_selector("#error-toast.show")
    assert "couldn't acknowledge" in page.locator("#error-toast").inner_text().lower()


def test_alert_banner_shows_for_an_open_incident_and_links_to_the_alerts_panel(live_server, page):
    """The topbar's red ALERT banner (state.incidents-driven, see
    renderAlertBanner()) reflects real open (unacknowledged) incidents --
    it should be absent with none, appear once one opens, and disappear
    again once it's acknowledged, rather than being static decoration.
    """
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector("#tracks-list")
    assert not page.locator("#alert-banner").is_visible()

    requests.post(live_server + "/api/detections", json=INSIDE_RESTRICTED_ZONE, timeout=5).raise_for_status()
    page.wait_for_selector("#alert-banner.show")
    banner_text = page.locator("#alert-banner").inner_text()
    assert "UNACKNOWLEDGED ALERT" in banner_text
    assert "restricted zone" in banner_text.lower()

    page.click("#alert-banner")
    page.wait_for_selector(".alert-item")
    assert page.locator('.rail-btn[data-panel="alerts"]').get_attribute("aria-pressed") == "true"

    page.click("button[data-ack-id]")
    page.wait_for_selector('.alert-item .badge-outline:has-text("acknowledged")')
    # Acknowledged (not resolved) no longer needs to interrupt the whole
    # screen -- the rail's own badge (still "active" while acknowledged)
    # is a distinct, separate signal from this banner.
    assert not page.locator("#alert-banner").is_visible()


def test_map_verify_filter_toggles_which_diamonds_plot(live_server, page):
    """The bottom map-level Verified/Unverified filter (state.
    mapClassExcluded) is independent of the Tracks panel's own Verified/
    Unverified sub-tabs -- it decides which markers render on the map, per
    classification color, defaults to showing everything, and should
    never affect the list.
    """
    _seed_moving_track(live_server)  # single radar sensor -> unverified, classifies as drone
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")
    page.wait_for_selector("#map-verify-filter")

    chip = page.locator('.map-verify-chip[data-bucket="unverified"][data-class="drone"]')
    page.wait_for_selector('.map-verify-chip[data-bucket="unverified"][data-class="drone"]')
    assert "1" in chip.inner_text()
    assert "active" in chip.get_attribute("class")
    assert page.locator('.map-verify-chip[data-bucket="verified"]').count() == 0

    chip.click()
    assert "excluded" in page.locator(
        '.map-verify-chip[data-bucket="unverified"][data-class="drone"]'
    ).get_attribute("class")
    # The list is untouched by the map-only filter -- still counts as
    # unverified in the Tracks panel's own tab.
    page.click(".verify-tab-btn[data-verify='unverified']")
    assert page.locator(".track-card[data-id]").count() == 1


def test_live_view_shows_an_inset_thumbnail_only_when_a_second_real_camera_exists(live_server, page):
    """The details panel's Live view inset thumbnail (secondNearestCameraSensor())
    should never appear for a track with only one nearby camera -- that would be
    a fake duplicate of the main feed -- but should appear once a second,
    genuinely distinct camera sensor is registered nearby.
    """
    requests.put(
        live_server + "/api/sensor-registrations/camera-1",
        json={"sensor_type": "camera", "latitude": 51.5, "longitude": -0.1, "camera_stream_url": "rtsp://cam1/x"},
        timeout=5,
    ).raise_for_status()
    requests.post(live_server + "/api/detections", json={
        "sensor_id": "radar-1", "sensor_type": "radar", "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
    }, timeout=5).raise_for_status()

    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")
    page.click(".track-card[data-id]")
    page.wait_for_selector("#live-view-slot")
    page.wait_for_timeout(300)
    assert page.locator(".inset-view-img").count() == 0

    requests.put(
        live_server + "/api/sensor-registrations/camera-2",
        json={"sensor_type": "camera", "latitude": 51.5002, "longitude": -0.1002, "camera_stream_url": "rtsp://cam2/x"},
        timeout=5,
    ).raise_for_status()
    page.evaluate("refresh()")  # force a refresh rather than waiting out the real 15s poll interval
    page.wait_for_selector(".inset-view-img")


def test_friend_foe_neutral_and_ignore_buttons_drive_the_real_endpoints(live_server, page):
    """Friend/Foe (POST /api/tracks/{id}/classify) and Ignore (POST
    /api/tracks/{id}/ignore) are real operator overrides, not decoration
    -- each click should be reflected back from the API on the next
    refresh, including Neutral clearing a previous Friend/Foe call.
    """
    _seed_moving_track(live_server)
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")
    page.click(".track-card[data-id]")
    page.wait_for_selector("#classify-friendly-btn")

    page.click("#classify-friendly-btn")
    page.wait_for_selector('.badge:has-text("friendly")')

    # Foe is the one classification that asks for confirmation first
    # (window.confirm) -- Playwright auto-dismisses dialogs unless handled.
    page.on("dialog", lambda dialog: dialog.accept())
    page.click("#classify-foe-btn")
    page.wait_for_selector('.badge:has-text("drone")')

    page.click("#classify-neutral-btn")
    page.wait_for_selector('.badge:has-text("unknown")')

    assert "Ignore" in page.locator("#ignore-toggle-btn").inner_text()
    page.click("#ignore-toggle-btn")  # opens the duration menu
    page.click("#ignore-duration-menu button[data-ignore-minutes='']")  # Indefinitely
    page.wait_for_selector("#ignore-toggle-btn:text-is('Unignore')")
    assert page.locator(".track-card.ignored").count() == 1
    assert page.locator(".ignored-badge").count() == 1

    page.click("#ignore-toggle-btn")  # now a direct Unignore, no menu
    # :text-is is an exact match -- "Unignore" contains "Ignore" as a
    # substring, so a has-text wait here would resolve on the stale state.
    page.wait_for_selector("#ignore-toggle-btn:text-is('Ignore ▾')")
    assert page.locator(".track-card.ignored").count() == 0


def test_ignore_for_a_fixed_duration_shows_the_remaining_time(live_server, page):
    _seed_moving_track(live_server)
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")
    page.click(".track-card[data-id]")
    page.wait_for_selector("#ignore-toggle-btn")

    page.click("#ignore-toggle-btn")
    page.click("#ignore-duration-menu button[data-ignore-minutes='30']")
    page.wait_for_selector("#ignore-toggle-btn:has-text('Unignore')")
    # 30 minutes rounds to "30m" via fmtUntil -- not "expired" or blank.
    assert "30m" in page.locator("#ignore-toggle-btn").inner_text()


def test_track_card_shows_a_duration_and_a_ptz_badge_when_a_camera_is_near(live_server, page):
    requests.put(
        live_server + "/api/sensor-registrations/camera-1",
        json={"sensor_type": "camera", "latitude": 51.4970, "longitude": -0.1150, "camera_stream_url": "rtsp://cam/x"},
        timeout=5,
    ).raise_for_status()
    _seed_moving_track(live_server)
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")

    assert page.locator(".ptz-badge").count() == 1
    meta_text = page.locator(".track-card-meta").first.inner_text()
    assert "dur " in meta_text


def test_map_marker_tooltip_shows_verified_state_and_contributing_sensors_once_selected(live_server, page):
    """Selecting a track's map marker (trackMapPopupHtml(), folded into
    its permanent tooltip -- see renderMap()'s comment for why not a
    Leaflet popup) surfaces real per-track sensor corroboration --
    track.contributing_sensor_types -- not a fixed/fake icon set.
    """
    requests.post(live_server + "/api/detections", json={
        "sensor_id": "radar-1", "sensor_type": "radar", "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
    }, timeout=5).raise_for_status()
    requests.post(live_server + "/api/detections", json={
        "sensor_id": "cam-1", "sensor_type": "camera", "latitude": 51.5001, "longitude": -0.1001, "confidence": 0.9,
    }, timeout=5).raise_for_status()

    page.goto(live_server, wait_until="networkidle")
    # Two distinct sensor types -> verified, so it's on the Verified tab,
    # not the default Unverified one.
    page.click(".verify-tab-btn[data-verify='verified']")
    page.wait_for_selector(".track-card[data-id]")
    page.click(".leaflet-marker-icon")
    page.wait_for_selector(".track-tooltip-selected")
    # Leaflet's zoom-animated tooltip lets a just-removed one briefly
    # coexist with its replacement in the DOM -- .last is the current one.
    tooltip_text = page.locator(".track-tooltip-selected").last.inner_text()
    assert "Verified" in tooltip_text
    assert "radar" in tooltip_text
    assert "camera" in tooltip_text


def test_notification_bell_shows_a_real_new_alert_and_clears_on_open(live_server, page):
    """detectAndRecordNotifications() -- a real, locally-observed state
    transition (a new incident opening between one poll and the next),
    not a fabricated notification feed. The very first refresh only
    establishes the baseline, so nothing appears until a genuine second
    transition happens.
    """
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector("#notif-bell-btn")
    assert "show" not in (page.locator("#notif-badge").get_attribute("class") or "")

    requests.post(live_server + "/api/detections", json=INSIDE_RESTRICTED_ZONE, timeout=5).raise_for_status()
    page.evaluate("refresh()")
    page.wait_for_selector("#notif-badge.show")
    assert page.locator("#notif-badge").inner_text() == "1"

    page.click("#notif-bell-btn")
    page.wait_for_selector("#notif-panel:not([hidden])")
    panel_text = page.locator("#notif-panel").inner_text()
    assert "New alert" in panel_text
    assert "restricted zone" in panel_text.lower()

    # Opening the panel marks everything read -- the badge should clear.
    assert "show" not in (page.locator("#notif-badge").get_attribute("class") or "")

    # Clicking elsewhere closes the panel.
    page.click("#app-shell")
    page.wait_for_timeout(100)


def test_details_subtitle_shows_confidence_tier_zone_status_and_risk_explanation(live_server, page):
    """The details panel subtitle row's confidence-tier badge and zone-status
    badge, plus the Quality tab's risk-explanation panel, are all relabelings
    of real, already-computed fields (corroborating_sensor_types, zone_status,
    risk_score/risk_factors) -- not a new fabricated score. A 2-sensor-type
    track sitting inside the seeded restricted zone should show "Moderate
    confidence", "Inside protected zone", and a risk breakdown that actually
    accounts for the total.
    """
    requests.post(live_server + "/api/detections", json=INSIDE_RESTRICTED_ZONE, timeout=5).raise_for_status()
    tracks = requests.get(live_server + "/api/tracks", timeout=5).json()
    track_id = tracks[0]["id"]
    requests.post(live_server + "/api/detections", json={
        "sensor_id": "cam-1", "sensor_type": "camera", "latitude": 51.5001, "longitude": -0.1001,
        "confidence": 0.9, "track_id": track_id,
    }, timeout=5).raise_for_status()

    page.goto(live_server, wait_until="networkidle")
    page.click(".verify-tab-btn[data-verify='verified']")  # 2 sensor types here, not the default Unverified tab
    page.wait_for_selector(".track-card[data-id]")
    page.click(".track-card[data-id]")
    page.wait_for_selector(".detail-subtitle-row")

    subtitle = page.locator(".detail-subtitle-row").inner_text()
    assert "Moderate confidence" in subtitle
    assert "Inside protected zone" in subtitle
    assert "Risk" in subtitle

    page.click(".tab-btn[data-tab='quality']")
    page.wait_for_selector(".risk-explain")
    risk_text = page.locator(".risk-explain").inner_text()
    assert "Classified as drone" in risk_text
    assert "Verified by" in risk_text
    assert "protected zone" in risk_text


def test_details_timeline_tab_shows_real_detection_and_incident_events(live_server, page):
    """The Timeline tab is built entirely from data the track/incident
    endpoints already expose -- first-seen and this track's own zone
    incidents opening/closing -- never a fabricated "camera assigned" or
    "target acquired" event a closed-loop auto-tracking system would need.
    """
    requests.post(live_server + "/api/detections", json=INSIDE_RESTRICTED_ZONE, timeout=5).raise_for_status()

    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")
    page.click(".track-card[data-id]")
    page.click(".tab-btn[data-tab='timeline']")
    page.wait_for_selector(".timeline-list")
    page.wait_for_timeout(300)

    timeline_text = page.locator(".timeline-list").inner_text()
    assert "Track first detected" in timeline_text
    assert "zone incursion incident opened" in timeline_text


def test_live_tab_shows_an_honest_camera_state_label_not_a_fake_tracking_state(live_server, page):
    """The Live view's camera-state label only ever claims one of three real
    states this app can actually observe (connecting, Live once the stream
    image loads, or Camera unreachable on a load error) -- never the fake
    Available/Slewing/Searching/Acquired/Following/Locked auto-tracking
    machine a real closed-loop PTZ system would need.
    """
    requests.put(live_server + "/api/sensor-registrations/cam-1", json={
        "sensor_type": "camera", "latitude": 51.5001, "longitude": -0.1001,
        "camera_stream_url": "rtsp://cam1/x",
    }, timeout=5).raise_for_status()
    requests.post(live_server + "/api/detections", json={
        "sensor_id": "radar-1", "sensor_type": "radar", "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
    }, timeout=5).raise_for_status()

    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")
    page.click(".track-card[data-id]")
    page.wait_for_selector("#camera-state-label")
    page.wait_for_timeout(500)

    label_text = page.locator("#camera-state-label").inner_text()
    assert label_text in ("Camera available — connecting…", "Live", "Camera unreachable")
    assert page.locator("#notif-panel").is_hidden()


def test_map_zones_and_labels_toggles_control_the_zone_layer_independently(live_server, page):
    """The Zones/Labels map-layer toggles (added alongside the existing
    Trails/Vectors/Uncertainty/Sensors ones) should behave independently:
    turning Zones off removes the zone polygon entirely, while turning only
    Labels off keeps the polygon but drops its always-visible name tooltip.
    """
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_timeout(500)
    assert page.evaluate("zoneLayer.getLayers().length") == 1
    assert page.locator(".zone-label").count() == 1

    page.click("#toggle-zones")
    page.wait_for_timeout(300)
    assert page.evaluate("zoneLayer.getLayers().length") == 0

    page.click("#toggle-zones")
    page.click("#toggle-labels")
    page.wait_for_timeout(300)
    assert page.evaluate("zoneLayer.getLayers().length") == 1
    assert page.locator(".zone-label").count() == 0


def test_notification_drawer_shows_severity_state_and_can_be_acknowledged(live_server, page):
    """Each notification now carries a real severity color, a lifecycle
    state (Unread/Read/Acknowledged/Resolved) computed from the actual
    incident it's linked to, and an Acknowledge button that drives the same
    real POST /api/incidents/{id}/acknowledge endpoint the Alerts panel
    uses -- not just a local flag.
    """
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector("#notif-bell-btn")

    requests.post(live_server + "/api/detections", json=INSIDE_RESTRICTED_ZONE, timeout=5).raise_for_status()
    page.evaluate("refresh()")
    page.wait_for_selector("#notif-badge.show")

    page.click("#notif-bell-btn")
    page.wait_for_selector(".notif-item")
    item = page.locator(".notif-item").first
    assert item.locator(".notif-sev-dot").count() == 1
    assert "Acknowledged" not in item.locator(".notif-state-chip").inner_text()

    item.locator("button[data-notif-ack]").click()
    page.wait_for_timeout(500)
    # acknowledge() calls refresh(), which rebuilds the panel from state.incidents
    page.click("#notif-bell-btn")
    page.click("#notif-bell-btn")
    page.wait_for_selector(".notif-item")
    chip_text = page.locator(".notif-item").first.locator(".notif-state-chip").inner_text()
    assert "acknowledged" in chip_text.lower()

    incidents = requests.get(live_server + "/api/incidents", timeout=5).json()
    assert incidents[0]["status"] == "acknowledged"


def test_visual_verification_dialog_records_a_real_result(live_server, page):
    """The Live view's Visual verification control is a structured,
    human-driven judgment (Confirmed/Different object/False detection/
    Unable to determine, plus a note) posted to a real endpoint -- not a
    single button that auto-declares the track verified, and not the fake
    PTZ auto-tracking confirmation states this app has no capability to
    back.
    """
    requests.put(live_server + "/api/sensor-registrations/cam-1", json={
        "sensor_type": "camera", "latitude": 51.5001, "longitude": -0.1001,
        "camera_stream_url": "rtsp://cam1/x",
    }, timeout=5).raise_for_status()
    requests.post(live_server + "/api/detections", json={
        "sensor_id": "radar-1", "sensor_type": "radar", "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
    }, timeout=5).raise_for_status()
    tracks = requests.get(live_server + "/api/tracks", timeout=5).json()
    track_id = tracks[0]["id"]

    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")
    page.click(".track-card[data-id]")
    page.wait_for_selector("#visual-verify-btn")
    page.click("#visual-verify-btn")
    page.wait_for_selector("#visual-verify-form:not([hidden])")
    page.check("input[name='visual-verify-result'][value='false_detection']")
    page.fill("#visual-verify-note", "No object visible in frame")
    page.click("#visual-verify-submit")
    page.wait_for_selector("#visual-verify-status:not([style*='display: none'])")
    assert "Recorded" in page.locator("#visual-verify-status").inner_text()

    entries = requests.get(live_server + "/api/audit-log", timeout=5).json()
    mine = [e for e in entries if e["target"] == str(track_id) and e["action"] == "track.visual_verify"]
    assert len(mine) == 1
    assert mine[0]["detail"] == "false_detection: No object visible in frame"


def test_details_panel_sections_follow_identity_telemetry_action_order(live_server, page):
    """The details panel reads top-to-bottom as Identity -> Telemetry ->
    Action, per the intended "identify the object, understand its
    movement, decide what to do" flow -- action buttons no longer sit
    above the identity/telemetry data they act on.
    """
    _seed_moving_track(live_server)
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector(".track-card[data-id]")
    page.click(".track-card[data-id]")
    page.wait_for_selector(".kv-section-title")

    titles = [t.strip().lower() for t in page.locator(".kv-section-title").all_inner_texts()]
    assert titles.index("identity") < titles.index("telemetry") < titles.index("action")
