"""Pixel-level regression tests for dashboard components -- what
test_dashboard.py's behavioral tests (clicks, selectors, ARIA attributes)
structurally can't catch: a CSS change that leaves every selector, click
target, and attribute correct while quietly breaking the *layout* (the
kind of bug the rail-badge-positioning regression documented in
app/static/dashboard.html's history was -- `.rail-btn .rail-badge` was
correctly present with the right selector and the right text, just
positioned relative to the wrong ancestor).

Targets are deliberately narrow, static UI chrome -- not the map (real
tile imagery, non-deterministic) and not the track list (live "Ns ago"
timestamps) -- so a failure here means the *layout* changed, not that a
clock ticked between the baseline and this run.

See assert_visual_baseline (tests_e2e/conftest.py) and this directory's
README for how to review and regenerate a baseline.
"""

from __future__ import annotations

import requests

# Inside app/zones.seed.json's bundled "Central London Restricted Zone"
# polygon (lat 51.49-51.51, lon -0.11..-0.09) -- landing a detection here
# deterministically opens exactly one incident, which is what puts the
# alert count on the rail badge this test is actually checking the
# position of.
DETECTION_INSIDE_RESTRICTED_ZONE = {
    "sensor_id": "radar-1", "sensor_type": "radar",
    "latitude": 51.5, "longitude": -0.10, "confidence": 0.9,
}


def test_visual_regression_icon_rail_with_active_alert(live_server, page, assert_visual_baseline):
    requests.post(f"{live_server}/api/detections", json=DETECTION_INSIDE_RESTRICTED_ZONE, timeout=5)
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector("#alerts-badge.show")

    assert_visual_baseline(page.locator("#icon-rail"), "icon_rail_with_active_alert")


def test_visual_regression_map_layer_toggles(live_server, page, assert_visual_baseline):
    page.goto(live_server, wait_until="networkidle")
    page.wait_for_selector("#toggle-trail")

    assert_visual_baseline(page.locator("#map-layer-toggles"), "map_layer_toggles")
