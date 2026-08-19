from datetime import datetime, timedelta

from app.db import create_track, get_track
from app.models import Classification, Track, TrackStatus
from app.tracking import expire_stale_tracks

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


def make_track(**overrides) -> Track:
    defaults = dict(
        track_uid="t1", first_seen=BASE_TIME, last_seen=BASE_TIME,
        status=TrackStatus.ACTIVE, classification=Classification.DRONE,
    )
    defaults.update(overrides)
    return create_track(Track(**defaults))


def test_active_track_stays_active_before_stale_timeout():
    track = make_track()
    expire_stale_tracks(now=BASE_TIME + timedelta(seconds=10))
    assert get_track(track.id).status == TrackStatus.ACTIVE


def test_active_track_goes_lost_after_stale_timeout():
    track = make_track()
    # Default DRONE_TRACK_STALE_SECONDS is 30.
    expire_stale_tracks(now=BASE_TIME + timedelta(seconds=31))
    assert get_track(track.id).status == TrackStatus.LOST


def test_lost_track_goes_closed_after_drop_timeout():
    track = make_track()
    expire_stale_tracks(now=BASE_TIME + timedelta(seconds=31))
    assert get_track(track.id).status == TrackStatus.LOST
    # Default DRONE_TRACK_DROP_SECONDS is 300, measured from last_seen.
    expire_stale_tracks(now=BASE_TIME + timedelta(seconds=331))
    assert get_track(track.id).status == TrackStatus.CLOSED


def test_closed_track_is_not_reconsidered():
    track = make_track()
    expire_stale_tracks(now=BASE_TIME + timedelta(seconds=31))
    expire_stale_tracks(now=BASE_TIME + timedelta(seconds=331))
    assert get_track(track.id).status == TrackStatus.CLOSED
    # A further sweep shouldn't error or change anything about a closed track.
    expire_stale_tracks(now=BASE_TIME + timedelta(seconds=1000))
    assert get_track(track.id).status == TrackStatus.CLOSED
