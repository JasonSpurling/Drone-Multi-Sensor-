"""GET /api/sensors/{id}/live -- the MJPEG live-view proxy (app/api/
camera_live.py). This environment's requirements.txt doesn't include
opencv (that's requirements-camera.txt, an optional extra -- see that
module's docstring for why), so most of these deliberately exercise the
"extras not installed" and validation paths rather than a real video
stream; the one happy-path test injects a fake cv2 module rather than
requiring the real dependency or a real camera.
"""

import sys
import types

import pytest
from fastapi.testclient import TestClient

from app.main import app

CAMERA_REGISTRATION = {
    "sensor_type": "camera", "latitude": 51.5, "longitude": -0.1,
    "camera_stream_url": "rtsp://camera.example/stream1",
}


def test_live_view_404s_for_unregistered_sensor(isolated_db):
    with TestClient(app) as client:
        r = client.get("/api/sensors/does-not-exist/live")
        assert r.status_code == 404


def test_live_view_409s_when_no_stream_url_configured(isolated_db):
    with TestClient(app) as client:
        client.put(
            "/api/sensor-registrations/cam-1",
            json={"sensor_type": "camera", "latitude": 51.5, "longitude": -0.1},
        )
        r = client.get("/api/sensors/cam-1/live")
        assert r.status_code == 409


def test_camera_stream_url_is_never_returned_by_the_registration_apis(isolated_db):
    with TestClient(app) as client:
        put_response = client.put("/api/sensor-registrations/cam-1", json=CAMERA_REGISTRATION)
        # The key can still appear (FastAPI's response_model re-fills a
        # stripped optional field with its schema default when
        # serializing) -- what actually matters, and what this asserts,
        # is that the real URL/credentials never come back, not that the
        # JSON key is literally absent.
        assert put_response.json().get("camera_stream_url") is None

        list_response = client.get("/api/sensor-registrations")
        assert all(row.get("camera_stream_url") is None for row in list_response.json())


def test_live_view_501s_without_the_camera_extras_installed(isolated_db, monkeypatch):
    # Deterministic regardless of whether opencv happens to be installed in
    # whatever environment actually runs this test -- see module docstring.
    monkeypatch.setitem(sys.modules, "cv2", None)
    with TestClient(app) as client:
        client.put("/api/sensor-registrations/cam-1", json=CAMERA_REGISTRATION)
        r = client.get("/api/sensors/cam-1/live")
        assert r.status_code == 501
        assert "requirements-camera.txt" in r.json()["detail"]


def test_live_view_streams_real_mjpeg_framing_with_a_fake_capture(isolated_db, monkeypatch):
    """Injects a fake cv2 module (a stand-in VideoCapture yielding one
    frame then ending) to test the actual MJPEG multipart framing
    (_mjpeg_frames) without needing real OpenCV or a real camera --
    the boundary/Content-Type/Content-Length structure is what a browser
    <img> tag actually depends on to render each frame.
    """
    fake_frame = object()

    class FakeVideoCapture:
        def __init__(self, url):
            self.url = url
            self._read_count = 0

        def isOpened(self):
            return True

        def read(self):
            self._read_count += 1
            if self._read_count == 1:
                return True, fake_frame
            return False, None

        def release(self):
            pass

    fake_cv2 = types.SimpleNamespace(
        VideoCapture=FakeVideoCapture,
        imencode=lambda ext, frame, params: (True, types.SimpleNamespace(tobytes=lambda: b"\xff\xd8fakejpeg\xff\xd9")),
        IMWRITE_JPEG_QUALITY=1,
    )
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    with TestClient(app) as client:
        client.put("/api/sensor-registrations/cam-1", json=CAMERA_REGISTRATION)
        with client.stream("GET", "/api/sensors/cam-1/live") as r:
            assert r.status_code == 200
            assert r.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
            body = b"".join(r.iter_bytes())

    assert b"--frame" in body
    assert b"Content-Type: image/jpeg" in body
    assert b"\xff\xd8fakejpeg\xff\xd9" in body


@pytest.fixture
def keys(monkeypatch):
    import json

    mapping = {"view-key": "viewer", "admin-key": "admin"}
    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps(mapping))
    return mapping


def test_viewer_role_is_sufficient_to_request_a_live_view(isolated_db, keys, monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)  # 501, not 403 -- proves the role check passed first
    with TestClient(app) as client:
        client.put(
            "/api/sensor-registrations/cam-1",
            json=CAMERA_REGISTRATION,
            headers={"X-API-Key": "admin-key"},
        )
        r = client.get("/api/sensors/cam-1/live", headers={"X-API-Key": "view-key"})
        assert r.status_code == 501  # not 403 -- a viewer is allowed to request a live view


def test_live_view_accepts_the_api_key_as_a_query_param_too(isolated_db, keys, monkeypatch):
    """An <img>/<video> element pointed at this endpoint can't set a
    custom X-API-Key header -- see require_role_allow_query_key's
    docstring in app/auth.py -- so this endpoint specifically must also
    accept the same credential via ?api_key=, unlike every other endpoint
    in this app.
    """
    monkeypatch.setitem(sys.modules, "cv2", None)
    with TestClient(app) as client:
        client.put(
            "/api/sensor-registrations/cam-1",
            json=CAMERA_REGISTRATION,
            headers={"X-API-Key": "admin-key"},
        )
        no_header = client.get("/api/sensors/cam-1/live")
        assert no_header.status_code == 401

        via_query = client.get("/api/sensors/cam-1/live", params={"api_key": "view-key"})
        assert via_query.status_code == 501  # past auth -- 501 is the next check (no cv2), not 401/403

        wrong_key = client.get("/api/sensors/cam-1/live", params={"api_key": "not-a-real-key"})
        assert wrong_key.status_code == 401
