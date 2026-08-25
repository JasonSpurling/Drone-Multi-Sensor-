"""The dashboard's vendored third-party assets (app/static/vendor/) -- kept
off the public unpkg.com CDN so loading the dashboard has no external
network dependency and no third party in the trust chain (see app/main.py's
StaticFiles mount for the full reasoning). These tests exist to catch a
regression back to a CDN reference, or a vendored file going missing.
"""

from fastapi.testclient import TestClient

from app.main import app


def test_dashboard_references_the_vendored_leaflet_not_a_cdn():
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "/static/vendor/leaflet/leaflet.css" in response.text
    assert "/static/vendor/leaflet/leaflet.js" in response.text
    assert "unpkg.com" not in response.text
    assert "cdn.jsdelivr.net" not in response.text


def test_vendored_leaflet_js_is_served():
    with TestClient(app) as client:
        response = client.get("/static/vendor/leaflet/leaflet.js")
    assert response.status_code == 200
    assert int(response.headers["content-length"]) > 1000


def test_vendored_leaflet_css_is_served():
    with TestClient(app) as client:
        response = client.get("/static/vendor/leaflet/leaflet.css")
    assert response.status_code == 200
    assert int(response.headers["content-length"]) > 1000


def test_vendored_leaflet_marker_images_are_served():
    with TestClient(app) as client:
        for name in ("marker-icon.png", "marker-icon-2x.png", "marker-shadow.png"):
            response = client.get(f"/static/vendor/leaflet/images/{name}")
            assert response.status_code == 200, name


def test_favicon_is_served_at_the_conventional_path():
    # Regression test: browsers request /favicon.ico directly (not only
    # via dashboard.html's <link rel="icon">) -- unserved, this is a 404
    # console error on every single dashboard load.
    with TestClient(app) as client:
        response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"


def test_dashboard_references_a_favicon_link():
    with TestClient(app) as client:
        response = client.get("/")
    assert 'rel="icon"' in response.text
