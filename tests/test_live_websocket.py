"""GET /ws/live pushes track/incident updates to a connected client
without it waiting for a poll -- see app/live.py (the pub/sub) and
app/api/live.py (the endpoint). Uses TestClient's real WebSocket support
(a real ASGI connection over a real event loop, not a mock), which is
what actually exercises app.live.publish()'s thread-hop: the detection
POST below runs as a sync endpoint dispatched to a worker thread, while
the WebSocket read happens on the event loop -- if that hand-off were
broken, this test would hang until its own timeout rather than pass.
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app

DETECTION_BODY = {
    "sensor_id": "radar-1", "sensor_type": "radar",
    "latitude": 51.5, "longitude": -0.1, "confidence": 0.9,
}


def test_posting_a_detection_pushes_a_track_update_over_the_websocket(isolated_db):
    with TestClient(app) as client, client.websocket_connect("/ws/live") as ws:
        r = client.post("/api/detections", json=DETECTION_BODY)
        assert r.status_code == 201

        message = json.loads(ws.receive_text())
        assert message["type"] == "track_update"
        assert message["track"]["id"] == r.json()["track_id"]


def test_incident_opening_pushes_an_incident_opened_event(isolated_db):
    with TestClient(app) as client, client.websocket_connect("/ws/live") as ws:
        # Inside app/zones.seed.json's bundled restricted zone --
        # opens an incident, same as tests_e2e's own zone-incursion
        # fixtures.
        zone_body = {**DETECTION_BODY, "latitude": 51.5, "longitude": -0.10}
        r = client.post("/api/detections", json=zone_body)
        assert r.status_code == 201

        seen_types = set()
        for _ in range(5):  # the track_update for this same detection may arrive first
            message = json.loads(ws.receive_text())
            seen_types.add(message["type"])
            if "incident_opened" in seen_types:
                break
        assert "incident_opened" in seen_types


def test_a_key_scoped_to_a_different_site_never_receives_another_sites_events(monkeypatch):
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

    with TestClient(app) as client, client.websocket_connect("/ws/live?api_key=site-b-key") as ws_b:
        r = client.post(
            "/api/detections", json=DETECTION_BODY, headers={"X-API-Key": "site-a-key"}
        )
        assert r.status_code == 201

        # Site B's socket should see nothing from site A's detection --
        # prove that by having site B post its own, distinguishable
        # event and confirming *that* (not a leaked site-A event) is
        # what arrives.
        r_b = client.post(
            "/api/detections", json=DETECTION_BODY, headers={"X-API-Key": "site-b-key"}
        )
        assert r_b.status_code == 201

        message = json.loads(ws_b.receive_text())
        assert message["track"]["id"] == r_b.json()["track_id"]
        assert message["track"]["id"] != r.json()["track_id"]


def test_missing_api_key_is_rejected_when_keys_are_configured(monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setattr("app.config.API_KEY", "")
    monkeypatch.setattr("app.config.API_KEYS_JSON", json.dumps({"admin-key": "admin"}))

    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect("/ws/live"),
    ):
        pass

    assert exc_info.value.code == 4401
    assert "api_key" in exc_info.value.reason


class _FakeQueue:
    def __init__(self, items):
        self._items = list(items)

    async def get(self):
        return self._items.pop(0)


class _FakeWebSocket:
    def __init__(self, send_effect: Exception):
        self.accepted = False
        self._send_effect = send_effect

    async def accept(self):
        self.accepted = True

    async def send_text(self, payload):
        raise self._send_effect

    async def close(self, code: int = 1000, reason: str = "") -> None:
        pass


def test_live_updates_swallows_a_disconnect_that_happens_while_pushing_an_update(monkeypatch, site_id):
    from app.api.live import live_updates

    monkeypatch.setattr("app.api.live.subscribe", lambda site_id: _FakeQueue(["{}"]))
    monkeypatch.setattr("app.api.live.unsubscribe", lambda site_id, queue: None)
    ws = _FakeWebSocket(send_effect=WebSocketDisconnect())

    asyncio.run(live_updates(ws, api_key=None))  # must not raise

    assert ws.accepted


def test_live_updates_logs_and_survives_an_unexpected_send_failure(monkeypatch, site_id, caplog):
    from app.api.live import live_updates

    monkeypatch.setattr("app.api.live.subscribe", lambda site_id: _FakeQueue(["{}"]))
    monkeypatch.setattr("app.api.live.unsubscribe", lambda site_id, queue: None)
    ws = _FakeWebSocket(send_effect=RuntimeError("boom"))

    with caplog.at_level("ERROR"):
        asyncio.run(live_updates(ws, api_key=None))  # must not raise

    assert "Live-update WebSocket connection failed" in caplog.text
