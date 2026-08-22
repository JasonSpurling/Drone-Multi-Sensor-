from datetime import datetime, timedelta

from app.db import (
    create_detection,
    create_incident,
    create_track,
    get_track,
    list_audit_log,
    list_detections,
    list_tracks,
    purge_old_audit_log,
    purge_old_detections,
    purge_old_tracks,
    record_audit,
)
from app.models import (
    Detection,
    Incident,
    IncidentType,
    SensorType,
    Track,
    TrackStatus,
)


def make_detection(site_id: int, timestamp: datetime) -> Detection:
    return Detection(
        site_id=site_id, sensor_id="radar-1", sensor_type=SensorType.RADAR, timestamp=timestamp, confidence=0.9
    )


def make_track(site_id: int, last_seen: datetime, status: TrackStatus = TrackStatus.CLOSED) -> Track:
    return Track(
        site_id=site_id,
        track_uid=f"track-{last_seen.isoformat()}-{status.value}",
        first_seen=last_seen,
        last_seen=last_seen,
        status=status,
    )


def test_purge_removes_only_detections_older_than_cutoff(site_id):
    now = datetime(2026, 1, 10, 12, 0, 0)
    create_detection(make_detection(site_id, now - timedelta(days=40)))
    create_detection(make_detection(site_id, now - timedelta(days=20)))
    create_detection(make_detection(site_id, now - timedelta(days=1)))

    removed = purge_old_detections(before=now - timedelta(days=30))

    assert removed == 1
    remaining = list_detections(site_id=site_id)
    assert len(remaining) == 2
    assert all(d.timestamp >= now - timedelta(days=30) for d in remaining)


def test_purge_returns_zero_when_nothing_is_old_enough(site_id):
    now = datetime(2026, 1, 10, 12, 0, 0)
    create_detection(make_detection(site_id, now))
    assert purge_old_detections(before=now - timedelta(days=30)) == 0


def test_purge_old_tracks_removes_only_finished_tracks_past_the_cutoff(site_id):
    now = datetime(2026, 1, 10, 12, 0, 0)
    old_closed = create_track(make_track(site_id, now - timedelta(days=40), TrackStatus.CLOSED))
    recent_closed = create_track(make_track(site_id, now - timedelta(days=1), TrackStatus.CLOSED))
    old_active = create_track(make_track(site_id, now - timedelta(days=40), TrackStatus.ACTIVE))

    removed = purge_old_tracks(before=now - timedelta(days=30))

    assert removed == 1
    remaining_ids = {t.id for t in list_tracks(site_id=site_id)}
    assert remaining_ids == {recent_closed.id, old_active.id}
    assert old_closed.id not in remaining_ids


def test_purge_old_tracks_detaches_rather_than_deletes_detections_and_incidents(site_id):
    now = datetime(2026, 1, 10, 12, 0, 0)
    track = create_track(make_track(site_id, now - timedelta(days=40), TrackStatus.CLOSED))
    assert track.id is not None
    detection = create_detection(
        Detection(
            site_id=site_id,
            sensor_id="radar-1",
            sensor_type=SensorType.RADAR,
            timestamp=now - timedelta(days=40),
            track_id=track.id,
            confidence=0.9,
        )
    )
    incident = create_incident(
        Incident(site_id=site_id, incident_uid="inc-1", incident_type=IncidentType.LOITERING, track_id=track.id)
    )

    removed = purge_old_tracks(before=now - timedelta(days=30))

    assert removed == 1
    assert get_track(track.id, site_id=site_id) is None
    remaining_detections = list_detections(site_id=site_id)
    assert len(remaining_detections) == 1
    assert remaining_detections[0].id == detection.id
    assert remaining_detections[0].track_id is None
    assert incident.id is not None


def test_purge_old_tracks_returns_zero_when_nothing_is_old_enough(site_id):
    now = datetime(2026, 1, 10, 12, 0, 0)
    create_track(make_track(site_id, now, TrackStatus.CLOSED))
    assert purge_old_tracks(before=now - timedelta(days=30)) == 0


def test_purge_old_audit_log_removes_only_entries_older_than_cutoff(site_id):
    now = datetime(2026, 1, 10, 12, 0, 0)
    record_audit(site_id=site_id, actor="admin", action="zone.create", target="zone-1")
    entries_before = list_audit_log()
    assert len(entries_before) == 1

    # record_audit always timestamps with utcnow(), so directly backdate
    # the row it just wrote rather than mocking the clock.
    from sqlalchemy import text

    from app.db import db_session

    with db_session() as conn:
        conn.execute(
            text("UPDATE audit_log SET occurred_at = :occurred_at"),
            {"occurred_at": (now - timedelta(days=400)).isoformat()},
        )

    removed = purge_old_audit_log(before=now - timedelta(days=30))

    assert removed == 1
    assert list_audit_log() == []


def test_purge_old_audit_log_returns_zero_when_nothing_is_old_enough(site_id):
    now = datetime(2026, 1, 10, 12, 0, 0)
    record_audit(site_id=site_id, actor="admin", action="zone.create", target="zone-1")
    assert purge_old_audit_log(before=now - timedelta(days=30)) == 0
