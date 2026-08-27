"""POST/PUT /api/zones (app/api/zones.py) -- restricted zones used to be
editable only by hand-editing app/zones.seed.json and restarting the app;
these let an admin key create/update one at runtime instead.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

SQUARE = [(51.0, -0.1), (51.0, 0.1), (51.2, 0.1), (51.2, -0.1)]


@pytest.fixture
def admin_key(monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps({"admin-key": "admin"}))
    return "admin-key"


def test_create_zone(admin_key):
    with TestClient(app) as client:
        headers = {"X-API-Key": admin_key}
        r = client.post(
            "/api/zones",
            json={"name": "new-restricted-zone", "zone_type": "restricted", "polygon": SQUARE},
            headers=headers,
        )
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "new-restricted-zone"
        assert body["id"] is not None

        listed = client.get("/api/zones", headers=headers).json()
        assert any(z["name"] == "new-restricted-zone" for z in listed)


def test_create_zone_rejects_a_duplicate_name(admin_key):
    with TestClient(app) as client:
        headers = {"X-API-Key": admin_key}
        body = {"name": "dup-zone", "zone_type": "restricted", "polygon": SQUARE}
        assert client.post("/api/zones", json=body, headers=headers).status_code == 201
        r = client.post("/api/zones", json=body, headers=headers)
        assert r.status_code == 409


def test_create_zone_rejects_fewer_than_three_vertices(admin_key):
    with TestClient(app) as client:
        r = client.post(
            "/api/zones",
            json={"name": "bad-zone", "zone_type": "restricted", "polygon": [(51.0, -0.1), (51.2, 0.1)]},
            headers={"X-API-Key": admin_key},
        )
        assert r.status_code == 422


def test_create_zone_requires_admin_role(monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps({"view-key": "viewer"}))
    with TestClient(app) as client:
        r = client.post(
            "/api/zones",
            json={"name": "z", "zone_type": "restricted", "polygon": SQUARE},
            headers={"X-API-Key": "view-key"},
        )
        assert r.status_code == 403


def test_edit_zone_updates_the_polygon_and_is_reflected_in_incident_checks(admin_key):
    with TestClient(app) as client:
        headers = {"X-API-Key": admin_key}
        created = client.post(
            "/api/zones",
            json={"name": "movable-zone", "zone_type": "restricted", "polygon": SQUARE},
            headers=headers,
        ).json()

        # Move the zone somewhere else entirely.
        moved = [(10.0, 10.0), (10.0, 10.2), (10.2, 10.2), (10.2, 10.0)]
        r = client.put(
            f"/api/zones/{created['id']}",
            json={"name": "movable-zone", "zone_type": "restricted", "polygon": moved},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["polygon"] == [list(p) for p in moved]

        # A detection at the *old* polygon's location no longer incurs --
        # proves the edit actually took effect in the live incident
        # pipeline, not just in what GET /api/zones echoes back.
        detection = {
            "sensor_id": "radar-1", "sensor_type": "radar",
            "latitude": 51.1, "longitude": 0.0, "confidence": 0.9,
        }
        client.post("/api/detections", json=detection, headers=headers)
        assert client.get("/api/incidents", headers=headers).json() == []


def test_edit_zone_404s_for_a_zone_from_a_different_site(monkeypatch):
    from app.db import create_site

    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr(
        "app.config.API_KEYS_JSON",
        json.dumps(
            {
                "site-a-key": {"role": "admin", "site": "site-a"},
                "site-b-key": {"role": "admin", "site": "site-b"},
            }
        ),
    )
    create_site("site-a")
    create_site("site-b")

    with TestClient(app) as client:
        created = client.post(
            "/api/zones",
            json={"name": "site-a-zone", "zone_type": "restricted", "polygon": SQUARE},
            headers={"X-API-Key": "site-a-key"},
        ).json()

        r = client.put(
            f"/api/zones/{created['id']}",
            json={"name": "site-a-zone", "zone_type": "restricted", "polygon": SQUARE},
            headers={"X-API-Key": "site-b-key"},
        )
        assert r.status_code == 404


def test_get_zones_excludes_inactive_by_default_but_can_include_them(admin_key):
    with TestClient(app) as client:
        headers = {"X-API-Key": admin_key}
        created = client.post(
            "/api/zones",
            json={"name": "will-deactivate", "zone_type": "restricted", "polygon": SQUARE, "active": True},
            headers=headers,
        ).json()
        client.put(
            f"/api/zones/{created['id']}",
            json={"name": "will-deactivate", "zone_type": "restricted", "polygon": SQUARE, "active": False},
            headers=headers,
        )

        default_list = client.get("/api/zones", headers=headers).json()
        assert not any(z["name"] == "will-deactivate" for z in default_list)

        with_inactive = client.get("/api/zones?include_inactive=true", headers=headers).json()
        matching = [z for z in with_inactive if z["name"] == "will-deactivate"]
        assert len(matching) == 1
        assert matching[0]["active"] is False


def test_zone_mutations_are_recorded_in_the_audit_log(admin_key):
    with TestClient(app) as client:
        headers = {"X-API-Key": admin_key}
        created = client.post(
            "/api/zones",
            json={"name": "audited-zone", "zone_type": "restricted", "polygon": SQUARE},
            headers=headers,
        ).json()
        client.put(
            f"/api/zones/{created['id']}",
            json={"name": "audited-zone", "zone_type": "restricted", "polygon": SQUARE, "active": False},
            headers=headers,
        )

        entries = client.get("/api/audit-log", headers=headers).json()
        actions = [e["action"] for e in entries]
        assert "zone.create" in actions
        assert "zone.update" in actions
