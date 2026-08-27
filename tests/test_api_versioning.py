"""app.api_version + app.main's _ApiVersionMiddleware: header-based API
versioning (X-API-Version), not a /v1/ path prefix -- see that module's
docstring for why. Only one version exists today, so these tests cover
the mechanism (a client can pin a version, an unsupported one is
rejected, every response says what it got), not any version-specific
behavior difference (there isn't one yet).
"""

from fastapi.testclient import TestClient

from app.api_version import API_VERSION_HEADER, CURRENT_API_VERSION
from app.main import app


def test_api_response_carries_the_current_version_header_by_default():
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.headers[API_VERSION_HEADER] == CURRENT_API_VERSION


def test_a_request_pinned_to_the_current_version_is_accepted():
    with TestClient(app) as client:
        response = client.get("/api/health", headers={API_VERSION_HEADER: CURRENT_API_VERSION})
    assert response.status_code == 200
    assert response.headers[API_VERSION_HEADER] == CURRENT_API_VERSION


def test_a_request_for_an_unsupported_version_is_rejected():
    with TestClient(app) as client:
        response = client.get("/api/health", headers={API_VERSION_HEADER: "99"})
    assert response.status_code == 400
    assert "99" in response.json()["detail"]


def test_versioning_does_not_apply_to_the_dashboard_route():
    # / and /static/* aren't part of the versioned REST surface -- an
    # unsupported X-API-Version there must not 400 a page load.
    with TestClient(app) as client:
        response = client.get("/", headers={API_VERSION_HEADER: "99"})
    assert response.status_code == 200


def test_versioning_does_not_apply_to_vendored_static_assets():
    with TestClient(app) as client:
        response = client.get(
            "/static/vendor/leaflet/leaflet.js", headers={API_VERSION_HEADER: "99"}
        )
    assert response.status_code == 200
