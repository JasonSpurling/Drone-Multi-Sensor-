"""app.risk.compute_risk_score -- a plain point score, not a proprietary
ML model, composed entirely from classification/verified/open-incident-
severity, so this test suite pins down its documented arithmetic exactly.
"""

from datetime import datetime

from app.models import (
    Classification,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    IncidentType,
    Track,
    TrackStatus,
)
from app.risk import compute_risk_score

TRACK_DEFAULTS = {
    "track_uid": "t-1",
    "first_seen": datetime(2026, 1, 1, 12, 0, 0),
    "last_seen": datetime(2026, 1, 1, 12, 0, 0),
    "status": TrackStatus.ACTIVE,
}


def make_track(**overrides) -> Track:
    return Track(**{**TRACK_DEFAULTS, **overrides})


def make_incident(severity: IncidentSeverity) -> Incident:
    return Incident(
        incident_uid="i-1",
        incident_type=IncidentType.ZONE_INCURSION,
        severity=severity,
        status=IncidentStatus.OPEN,
        opened_at=datetime(2026, 1, 1, 12, 0, 0),
    )


def test_friendly_unverified_no_incidents_scores_zero():
    track = make_track(classification=Classification.FRIENDLY)
    assert compute_risk_score(track, []) == 0


def test_drone_classification_alone_scores_four():
    track = make_track(classification=Classification.DRONE)
    assert compute_risk_score(track, []) == 4


def test_verified_adds_two():
    track = make_track(classification=Classification.DRONE, verified=True)
    assert compute_risk_score(track, []) == 6


def test_open_incident_adds_its_severity():
    track = make_track(classification=Classification.DRONE)
    assert compute_risk_score(track, [make_incident(IncidentSeverity.HIGH)]) == 4 + 3


def test_multiple_open_incidents_use_the_worst_severity_not_the_sum():
    track = make_track(classification=Classification.DRONE)
    incidents = [make_incident(IncidentSeverity.LOW), make_incident(IncidentSeverity.CRITICAL)]
    assert compute_risk_score(track, incidents) == 4 + 4


def test_drone_verified_with_a_critical_incident_scores_ten():
    track = make_track(classification=Classification.DRONE, verified=True)
    assert compute_risk_score(track, [make_incident(IncidentSeverity.CRITICAL)]) == 10


def test_ignored_track_always_scores_zero_regardless_of_everything_else():
    track = make_track(classification=Classification.DRONE, verified=True, ignored=True)
    assert compute_risk_score(track, [make_incident(IncidentSeverity.CRITICAL)]) == 0
