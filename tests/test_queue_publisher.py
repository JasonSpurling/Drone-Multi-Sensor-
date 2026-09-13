import json
import logging
import socket
import threading
import time

import pytest

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
    afterwards (the CONNECT + PUB frames), and -- for consume()'s tests --
    replies to a SUB with whatever MSG frames are pushed via send_msg().
    Runs in a background thread so the publisher's/consumer's blocking
    socket calls have something to talk to, without requiring a real NATS
    binary or Docker in this environment. (This implementation has been
    cross-checked against a real `nats-server` binary during development
    -- see app/queue_publisher.py's consume() -- but ships as this
    dependency-free fake so CI doesn't need a NATS server installed.)
    """

    def __init__(self):
        self.received = b""
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.bind(("127.0.0.1", 0))
        self._server_socket.listen(1)
        self.port = self._server_socket.getsockname()[1]
        self._conn: socket.socket | None = None
        self._conn_ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        self._server_socket.settimeout(5)
        try:
            conn, _ = self._server_socket.accept()
        except OSError:
            return
        with conn:
            self._conn = conn
            conn.sendall(b'INFO {"server_id":"fake","version":"0.0.0"}\r\n')
            conn.settimeout(5)
            self._conn_ready.set()
            try:
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    self.received += chunk
            except OSError:
                pass

    def send_msg(self, subject: str, sid: str, payload: bytes) -> None:
        """Pushes a MSG frame to whatever client has SUB'd -- call after
        waiting for the client's SUB to arrive (poll `self.received`).
        """
        self._conn_ready.wait(timeout=5)
        assert self._conn is not None
        frame = f"MSG {subject} {sid} {len(payload)}\r\n".encode() + payload + b"\r\n"
        self._conn.sendall(frame)

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


def _wait_for_sub(server: _FakeNatsServer, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if b"SUB " in server.received:
            return
        time.sleep(0.02)
    raise AssertionError("client never sent SUB")


def test_consume_raises_immediately_when_nats_url_is_unset(monkeypatch):
    monkeypatch.setattr("app.queue_publisher.NATS_URL", "")
    try:
        queue_publisher.consume("some.subject", lambda payload: None)
    except RuntimeError as exc:
        assert "DRONE_NATS_URL" in str(exc)
    else:
        raise AssertionError("expected a RuntimeError")


def test_consume_delivers_messages_to_the_handler(monkeypatch):
    server = _FakeNatsServer()
    monkeypatch.setattr("app.queue_publisher.NATS_URL", f"nats://127.0.0.1:{server.port}")
    received = []
    stop_event = threading.Event()

    def handler(payload: bytes) -> None:
        received.append(json.loads(payload))
        stop_event.set()

    t = threading.Thread(
        target=queue_publisher.consume, args=("test.subject", handler), kwargs={"stop_event": stop_event}
    )
    t.start()
    try:
        _wait_for_sub(server)
        assert b"SUB test.subject 1\r\n" in server.received  # no queue group -> plain SUB
        server.send_msg("test.subject", "1", json.dumps({"hello": "world"}).encode())
        stop_event.wait(timeout=5)
    finally:
        stop_event.set()
        t.join(timeout=5)
        server.close()

    assert received == [{"hello": "world"}]


def test_consume_with_a_queue_group_sends_the_group_in_sub(monkeypatch):
    server = _FakeNatsServer()
    monkeypatch.setattr("app.queue_publisher.NATS_URL", f"nats://127.0.0.1:{server.port}")
    stop_event = threading.Event()

    t = threading.Thread(
        target=queue_publisher.consume,
        args=("test.subject", lambda payload: None),
        kwargs={"queue_group": "workers", "stop_event": stop_event},
    )
    t.start()
    try:
        _wait_for_sub(server)
        assert b"SUB test.subject workers 1\r\n" in server.received
    finally:
        stop_event.set()
        t.join(timeout=5)
        server.close()


def test_consume_replies_to_ping_with_pong(monkeypatch):
    # A real NATS server periodically PINGs and disconnects a client that
    # never PONGs back -- this is what keeps a long-lived consume() loop
    # from being silently dropped by the broker.
    server = _FakeNatsServer()
    monkeypatch.setattr("app.queue_publisher.NATS_URL", f"nats://127.0.0.1:{server.port}")
    stop_event = threading.Event()

    t = threading.Thread(
        target=queue_publisher.consume,
        args=("test.subject", lambda payload: None),
        kwargs={"stop_event": stop_event},
    )
    t.start()
    try:
        _wait_for_sub(server)
        server._conn.sendall(b"PING\r\n")

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and b"PONG\r\n" not in server.received:
            time.sleep(0.02)
        assert b"PONG\r\n" in server.received
    finally:
        stop_event.set()
        t.join(timeout=5)
        server.close()


def test_consume_stops_promptly_when_stop_event_is_set(monkeypatch):
    server = _FakeNatsServer()
    monkeypatch.setattr("app.queue_publisher.NATS_URL", f"nats://127.0.0.1:{server.port}")
    stop_event = threading.Event()

    t = threading.Thread(
        target=queue_publisher.consume,
        args=("test.subject", lambda payload: None),
        kwargs={"stop_event": stop_event},
    )
    t.start()
    try:
        _wait_for_sub(server)
    finally:
        stop_event.set()
        t.join(timeout=5)
        server.close()

    assert not t.is_alive()


def test_read_line_stops_on_a_socket_closed_before_the_terminator():
    server_sock, client_sock = socket.socketpair()
    server_sock.close()  # closed before ever sending a full CRLF-terminated line
    try:
        assert queue_publisher._read_line(client_sock) == b""
    finally:
        client_sock.close()


def test_subscribe_once_raises_when_the_server_closes_before_any_message(monkeypatch):
    server = _FakeNatsServer()
    monkeypatch.setattr("app.queue_publisher.NATS_URL", f"nats://127.0.0.1:{server.port}")
    stop_event = threading.Event()

    def close_after_sub():
        _wait_for_sub(server)
        server._conn.close()

    threading.Thread(target=close_after_sub, daemon=True).start()
    try:
        with pytest.raises(ConnectionError, match="closed by server"):
            queue_publisher._subscribe_once("test.subject", "", lambda payload: None, stop_event)
    finally:
        stop_event.set()
        server.close()


def test_subscribe_once_reassembles_a_message_payload_sent_across_multiple_packets(monkeypatch):
    # Real TCP delivery doesn't guarantee one MSG frame arrives in a single
    # recv() call -- this proves the buffering loop (which itself must
    # survive a recv() timeout while waiting for the rest) stitches it
    # back together correctly rather than only working when everything
    # happens to land in one chunk.
    server = _FakeNatsServer()
    monkeypatch.setattr("app.queue_publisher.NATS_URL", f"nats://127.0.0.1:{server.port}")
    stop_event = threading.Event()
    received = []

    def handler(payload: bytes) -> None:
        received.append(payload)
        stop_event.set()

    t = threading.Thread(
        target=queue_publisher._subscribe_once, args=("test.subject", "", handler, stop_event)
    )
    t.start()
    try:
        _wait_for_sub(server)
        payload = b'{"hello": "world"}'
        header = f"MSG test.subject 1 {len(payload)}\r\n".encode()
        server._conn.sendall(header + payload[:5])
        time.sleep(1.2)  # exceed _RECV_POLL_TIMEOUT_SECONDS so a recv() timeout is hit while waiting
        server._conn.sendall(payload[5:] + b"\r\n")
        stop_event.wait(timeout=5)
    finally:
        stop_event.set()
        t.join(timeout=5)
        server.close()

    assert received == [payload]


def test_subscribe_once_returns_promptly_if_stop_event_is_set_while_awaiting_message_bytes(monkeypatch):
    server = _FakeNatsServer()
    monkeypatch.setattr("app.queue_publisher.NATS_URL", f"nats://127.0.0.1:{server.port}")
    stop_event = threading.Event()

    t = threading.Thread(
        target=queue_publisher._subscribe_once, args=("test.subject", "", lambda payload: None, stop_event)
    )
    t.start()
    try:
        _wait_for_sub(server)
        # Claims a 20-byte payload but only ever sends 5 -- the rest never
        # arrives, so the only way out is noticing stop_event mid-wait.
        server._conn.sendall(b"MSG test.subject 1 20\r\n" + b"only5")
        time.sleep(0.3)
        stop_event.set()
        t.join(timeout=5)
    finally:
        stop_event.set()
        server.close()

    assert not t.is_alive()


def test_subscribe_once_raises_when_the_connection_drops_mid_message(monkeypatch):
    server = _FakeNatsServer()
    monkeypatch.setattr("app.queue_publisher.NATS_URL", f"nats://127.0.0.1:{server.port}")
    stop_event = threading.Event()

    def close_mid_message():
        _wait_for_sub(server)
        server._conn.sendall(b"MSG test.subject 1 50\r\n" + b"partial")
        server._conn.close()

    threading.Thread(target=close_mid_message, daemon=True).start()
    try:
        with pytest.raises(ConnectionError, match="mid-message"):
            queue_publisher._subscribe_once("test.subject", "", lambda payload: None, stop_event)
    finally:
        stop_event.set()
        server.close()


def test_subscribe_once_skips_unrecognized_protocol_lines(monkeypatch):
    # A real NATS server can send +OK/-ERR (or other frames this minimal
    # client doesn't act on) interleaved with MSG frames -- these must be
    # skipped, not mistaken for a MSG or crash the parser.
    server = _FakeNatsServer()
    monkeypatch.setattr("app.queue_publisher.NATS_URL", f"nats://127.0.0.1:{server.port}")
    stop_event = threading.Event()
    received = []

    def handler(payload: bytes) -> None:
        received.append(payload)
        stop_event.set()

    t = threading.Thread(
        target=queue_publisher._subscribe_once, args=("test.subject", "", handler, stop_event)
    )
    t.start()
    try:
        _wait_for_sub(server)
        payload = b'{"x": 1}'
        server._conn.sendall(b"+OK\r\n" + f"MSG test.subject 1 {len(payload)}\r\n".encode() + payload + b"\r\n")
        stop_event.wait(timeout=5)
    finally:
        stop_event.set()
        t.join(timeout=5)
        server.close()

    assert received == [payload]


def test_consume_creates_its_own_stop_event_when_none_is_given(monkeypatch):
    monkeypatch.setattr("app.queue_publisher.NATS_URL", "nats://127.0.0.1:4222")
    call_count = 0

    def fake_subscribe_once(subject, queue_group, handler, stop_event):
        nonlocal call_count
        call_count += 1
        stop_event.set()  # only reachable if consume() built a real, usable Event on our behalf
        raise OSError("simulated failure")

    monkeypatch.setattr("app.queue_publisher._subscribe_once", fake_subscribe_once)

    queue_publisher.consume("test.subject", lambda payload: None, reconnect_delay_seconds=0.01)

    assert call_count == 1


def test_consume_logs_and_waits_before_reconnecting_after_a_connection_error(monkeypatch, caplog):
    monkeypatch.setattr("app.queue_publisher.NATS_URL", "nats://127.0.0.1:4222")
    calls = []

    def fake_subscribe_once(subject, queue_group, handler, stop_event):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("dropped")
        stop_event.set()

    monkeypatch.setattr("app.queue_publisher._subscribe_once", fake_subscribe_once)
    stop_event = threading.Event()

    with caplog.at_level(logging.WARNING):
        queue_publisher.consume(
            "test.subject", lambda payload: None, stop_event=stop_event, reconnect_delay_seconds=0.01
        )

    assert len(calls) == 2
    assert "reconnecting" in caplog.text


def test_consume_survives_a_dropped_connection_and_keeps_retrying(monkeypatch):
    server = _FakeNatsServer()
    monkeypatch.setattr("app.queue_publisher.NATS_URL", f"nats://127.0.0.1:{server.port}")
    stop_event = threading.Event()

    t = threading.Thread(
        target=queue_publisher.consume,
        args=("test.subject", lambda payload: None),
        kwargs={"stop_event": stop_event, "reconnect_delay_seconds": 0.05},
    )
    t.start()
    try:
        _wait_for_sub(server)
        server._conn.close()  # simulate the broker dropping the connection

        # consume() must not crash or exit the loop on a dropped
        # connection -- it should still be alive, retrying, well after
        # the drop (real reconnection-to-a-second-listener behavior is
        # already covered end-to-end by consume()'s own manual
        # verification against a real nats-server binary during
        # development; this test only asserts the retry loop survives).
        time.sleep(0.5)
        assert t.is_alive()
    finally:
        stop_event.set()
        t.join(timeout=5)
        server.close()

    assert not t.is_alive()
