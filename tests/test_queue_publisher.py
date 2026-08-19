import socket
import threading

from app import queue_publisher


def test_disabled_by_default_makes_no_connection(monkeypatch):
    monkeypatch.setattr("app.queue_publisher.NATS_URL", "")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("should not attempt a connection when NATS_URL is unset")

    monkeypatch.setattr(socket, "create_connection", fail_if_called)
    queue_publisher.publish_detection({"sensor_id": "radar-1"})


class _FakeNatsServer:
    """A minimal stand-in for a NATS server: sends the INFO greeting a real
    client waits for, then records whatever bytes the client sends
    afterwards (the CONNECT + PUB frames). Runs in a background thread so
    the publisher's blocking socket calls have something to talk to,
    without requiring a real NATS binary or Docker in this environment.
    """

    def __init__(self):
        self.received = b""
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.bind(("127.0.0.1", 0))
        self._server_socket.listen(1)
        self.port = self._server_socket.getsockname()[1]
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        self._server_socket.settimeout(5)
        try:
            conn, _ = self._server_socket.accept()
        except OSError:
            return
        with conn:
            conn.sendall(b'INFO {"server_id":"fake","version":"0.0.0"}\r\n')
            conn.settimeout(5)
            try:
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    self.received += chunk
            except OSError:
                pass

    def close(self):
        self._server_socket.close()


def test_publish_sends_connect_and_pub_frames(monkeypatch):
    server = _FakeNatsServer()
    try:
        monkeypatch.setattr("app.queue_publisher.NATS_URL", f"nats://127.0.0.1:{server.port}")
        queue_publisher.publish("drone.detections", {"sensor_id": "radar-1", "confidence": 0.9})

        # Give the background thread a brief moment to receive the bytes.
        server._thread.join(timeout=2)
    finally:
        server.close()

    assert b"CONNECT {" in server.received
    assert b"PUB drone.detections " in server.received
    assert b'"sensor_id": "radar-1"' in server.received or b'"sensor_id":"radar-1"' in server.received


def test_unreachable_broker_does_not_raise(monkeypatch):
    # Nothing listening on this port -- connection should fail fast and be swallowed.
    monkeypatch.setattr("app.queue_publisher.NATS_URL", "nats://127.0.0.1:1")
    monkeypatch.setattr("app.queue_publisher.NATS_CONNECT_TIMEOUT_SECONDS", 0.5)
    queue_publisher.publish_incident({"incident_uid": "abc"})


def test_publish_detection_and_publish_incident_use_configured_subjects(monkeypatch):
    calls = []
    monkeypatch.setattr("app.queue_publisher.NATS_URL", "nats://127.0.0.1:4222")
    monkeypatch.setattr("app.queue_publisher.publish", lambda subject, payload: calls.append(subject))

    queue_publisher.publish_detection({"a": 1})
    queue_publisher.publish_incident({"b": 2})

    assert calls == [queue_publisher.NATS_DETECTION_SUBJECT, queue_publisher.NATS_INCIDENT_SUBJECT]
