"""The actual point of the site_id rearchitecture: two sites' data must
never leak into each other, whether through a list endpoint (wrong rows
silently included) or a get-by-id endpoint (a track/incident/zone id from
site A returned to a caller scoped to site B, or worse, edited by them).

Uses two real API keys, one per site, configured the same way a real
multi-site deployment would (DRONE_API_KEYS' per-key "site" field -- see
app/auth.py), not a shortcut that bypasses the auth layer these
guarantees actually depend on.
"""

import json

from fastapi.testclient import TestClient

from app.db import create_site, get_site
from app.main import app

SITE_A_KEY = "key-for-site-a-0123456789abcdef"
SITE_B_KEY = "key-for-site-b-0123456789abcdef"

DETECTION_BODY = {
    "sensor_id": "radar-1", "sensor_type": "radar",
    "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
}


def _configure_two_site_keys(monkeypatch):
    site_a = create_site("site-a")
    site_b = create_site("site-b")
    keys = {
        SITE_A_KEY: {"role": "admin", "site": "site-a"},
        SITE_B_KEY: {"role": "admin", "site": "site-b"},
    }
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps(keys))
    monkeypatch.setattr("app.config.API_KEY", "")
    return site_a, site_b


def test_tracks_created_at_site_a_are_invisible_to_site_b(monkeypatch):
    _configure_two_site_keys(monkeypatch)
    with TestClient(app) as client:
        r = client.post("/api/detections", json=DETECTION_BODY, headers={"X-API-Key": SITE_A_KEY})
        assert r.status_code == 201
        track_id = r.json()["track_id"]

        # Site A sees its own track.
        a_tracks = client.get("/api/tracks", headers={"X-API-Key": SITE_A_KEY}).json()
        assert len(a_tracks) == 1
        assert a_tracks[0]["id"] == track_id

        # Site B sees nothing.
        b_tracks = client.get("/api/tracks", headers={"X-API-Key": SITE_B_KEY}).json()
        assert b_tracks == []


def test_get_track_by_id_404s_for_a_different_sites_key(monkeypatch):
    _configure_two_site_keys(monkeypatch)
    with TestClient(app) as client:
        r = client.post("/api/detections", json=DETECTION_BODY, headers={"X-API-Key": SITE_A_KEY})
        track_id = r.json()["track_id"]

        own_site = client.get(f"/api/tracks/{track_id}", headers={"X-API-Key": SITE_A_KEY})
        assert own_site.status_code == 200

        # Not a 403 (which would confirm the track exists) -- a flat 404,
        # indistinguishable from an id that was never issued at all.
        other_site = client.get(f"/api/tracks/{track_id}", headers={"X-API-Key": SITE_B_KEY})
        assert other_site.status_code == 404


def test_incidents_do_not_leak_across_sites(monkeypatch):
    from app.db import create_zone
    from app.models import Zone, ZoneType

    site_a, _site_b = _configure_two_site_keys(monkeypatch)
    create_zone(
        Zone(
            site_id=site_a.id, name="rz", zone_type=ZoneType.RESTRICTED,
            polygon=[(51.0, -0.5), (51.0, 0.5), (52.0, 0.5), (52.0, -0.5)],
        )
    )

    with TestClient(app) as client:
        r = client.post("/api/detections", json=DETECTION_BODY, headers={"X-API-Key": SITE_A_KEY})
        assert r.status_code == 201

        a_incidents = client.get("/api/incidents", headers={"X-API-Key": SITE_A_KEY}).json()
        assert len(a_incidents) == 1

        b_incidents = client.get("/api/incidents", headers={"X-API-Key": SITE_B_KEY}).json()
        assert b_incidents == []

        # Site B's key can't acknowledge site A's incident either.
        incident_id = a_incidents[0]["id"]
        ack = client.post(f"/api/incidents/{incident_id}/acknowledge", headers={"X-API-Key": SITE_B_KEY})
        assert ack.status_code == 404


def test_zones_do_not_leak_across_sites(monkeypatch):
    from app.db import create_zone
    from app.models import Zone, ZoneType

    site_a, _site_b = _configure_two_site_keys(monkeypatch)
    create_zone(
        Zone(
            site_id=site_a.id, name="site-a-only-zone", zone_type=ZoneType.RESTRICTED,
            polygon=[(0, 0), (0, 1), (1, 1)],
        )
    )

    with TestClient(app) as client:
        a_zones = client.get("/api/zones", headers={"X-API-Key": SITE_A_KEY}).json()
        assert len(a_zones) == 1

        b_zones = client.get("/api/zones", headers={"X-API-Key": SITE_B_KEY}).json()
        assert b_zones == []


def test_new_detections_are_stamped_with_the_calling_keys_site_not_a_client_supplied_one(monkeypatch):
    # A client can't pick which site its own data lands in by putting a
    # site_id in the request body -- the server always uses the
    # authenticated key's site (app/api/detections.py), the same
    # reasoning as georeferenced being server-computed-only.
    site_a, site_b = _configure_two_site_keys(monkeypatch)
    with TestClient(app) as client:
        spoofed = {**DETECTION_BODY, "site_id": site_b.id}
        r = client.post("/api/detections", json=spoofed, headers={"X-API-Key": SITE_A_KEY})
        assert r.status_code == 201
        assert r.json()["site_id"] == site_a.id


def test_unknown_site_name_in_key_config_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "app.config.API_KEYS_JSON",
        json.dumps({SITE_A_KEY: {"role": "admin", "site": "no-such-site"}}),
    )
    monkeypatch.setattr("app.config.API_KEY", "")
    with TestClient(app) as client:
        r = client.get("/api/tracks", headers={"X-API-Key": SITE_A_KEY})
        assert r.status_code == 500


def test_site_wide_rate_limit_does_not_leak_across_sites(monkeypatch):
    # The site-wide detection rate limiter (app/ratelimit.py's
    # site_detection_rate_limiter, on top of the per-sensor one) is keyed
    # by site_id specifically so one site's sensors collectively maxing
    # out their budget can't starve a different site's -- a single
    # deployment-wide bucket would fail exactly this case.
    _configure_two_site_keys(monkeypatch)
    from app.ratelimit import RateLimiter

    monkeypatch.setattr("app.api.detections.site_detection_rate_limiter", RateLimiter(1.0, 1.0))

    with TestClient(app) as client:
        first_a = client.post("/api/detections", json=DETECTION_BODY, headers={"X-API-Key": SITE_A_KEY})
        assert first_a.status_code == 201
        second_a = client.post("/api/detections", json=DETECTION_BODY, headers={"X-API-Key": SITE_A_KEY})
        assert second_a.status_code == 429

        # Site B's own budget is untouched by site A exhausting its own.
        first_b = client.post("/api/detections", json=DETECTION_BODY, headers={"X-API-Key": SITE_B_KEY})
        assert first_b.status_code == 201


def test_legacy_bare_role_string_key_still_resolves_to_the_default_site(monkeypatch):
    # Backward compatibility: DRONE_API_KEYS' pre-multi-site format was
    # {"key": "role"} (a bare string), not {"key": {"role": ..., "site":
    # ...}}. That format must keep working unchanged, resolving to the
    # default site -- an existing deployment's config shouldn't need
    # editing just because this app now supports multiple sites.
    from app.sites import DEFAULT_SITE_NAME, ensure_default_site

    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps({SITE_A_KEY: "admin"}))
    monkeypatch.setattr("app.config.API_KEY", "")
    with TestClient(app) as client:
        r = client.post("/api/detections", json=DETECTION_BODY, headers={"X-API-Key": SITE_A_KEY})
        assert r.status_code == 201
        assert r.json()["site_id"] == ensure_default_site()
        assert DEFAULT_SITE_NAME == "default"


def test_get_site_returns_a_real_row_by_id_or_none_when_missing(isolated_db):
    site = create_site("site-lookup-test")
    assert site.id is not None

    fetched = get_site(site.id)
    assert fetched is not None
    assert fetched.id == site.id
    assert fetched.name == "site-lookup-test"

    assert get_site(999999) is None
