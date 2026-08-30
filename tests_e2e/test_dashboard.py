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
