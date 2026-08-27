from fastapi.testclient import TestClient

from app.main import app


def test_list_sites_includes_the_auto_created_default_site():
    with TestClient(app) as client:
        r = client.get("/api/sites")
        assert r.status_code == 200
        names = [s["name"] for s in r.json()]
        assert "default" in names


def test_create_site_persists_and_is_listed():
    with TestClient(app) as client:
        r = client.post("/api/sites", json={"name": "new-site"})
        assert r.status_code == 201
        assert r.json()["name"] == "new-site"
        assert r.json()["id"] is not None

        names = [s["name"] for s in client.get("/api/sites").json()]
        assert "new-site" in names


def test_create_duplicate_site_name_is_rejected():
    with TestClient(app) as client:
        first = client.post("/api/sites", json={"name": "dup-site"})
        assert first.status_code == 201
        second = client.post("/api/sites", json={"name": "dup-site"})
        assert second.status_code == 409
