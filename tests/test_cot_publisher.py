from datetime import datetime

from app import cot_publisher
from app.models import Classification, Track, TrackStatus

TRACK = Track(
    id=1, track_uid="abc-123-def",
    first_seen=datetime(2026, 1, 1), last_seen=datetime(2026, 1, 1),
    status=TrackStatus.ACTIVE, classification=Classification.DRONE,
    latitude=51.5, longitude=-0.1, altitude_m=120.0,
)


def test_disabled_by_default_sends_nothing(monkeypatch):
    monkeypatch.setattr("app.cot_publisher.COT_UDP_HOST", "")
    monkeypatch.setattr("socket.socket", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not connect")))
    cot_publisher.publish_track_cot(TRACK)


def test_sends_udp_datagram_when_configured(monkeypatch):
    sent = []

    class _FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def sendto(self, data, addr):
            sent.append((data, addr))

    monkeypatch.setattr("app.cot_publisher.COT_UDP_HOST", "127.0.0.1")
    monkeypatch.setattr("app.cot_publisher.COT_UDP_PORT", 6969)
    monkeypatch.setattr("socket.socket", lambda *a, **k: _FakeSocket())

    cot_publisher.publish_track_cot(TRACK)

    assert len(sent) == 1
    data, addr = sent[0]
    assert addr == ("127.0.0.1", 6969)
    assert b"<event" in data
    assert b'uid="drone-multi-sensor.abc-123-def"' in data


def test_track_without_position_sends_nothing(monkeypatch):
    monkeypatch.setattr("app.cot_publisher.COT_UDP_HOST", "127.0.0.1")

    def fail_if_called(*a, **k):
        raise AssertionError("should not open a socket for a track with no position")

    monkeypatch.setattr("socket.socket", fail_if_called)

    track = TRACK.model_copy(update={"latitude": None, "longitude": None})
    cot_publisher.publish_track_cot(track)


def test_socket_error_does_not_raise(monkeypatch):
    class _FailingSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def sendto(self, data, addr):
            raise OSError("network unreachable")

    monkeypatch.setattr("app.cot_publisher.COT_UDP_HOST", "127.0.0.1")
    monkeypatch.setattr("socket.socket", lambda *a, **k: _FailingSocket())

    cot_publisher.publish_track_cot(TRACK)  # must not raise
