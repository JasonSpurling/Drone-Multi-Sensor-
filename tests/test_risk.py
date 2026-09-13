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
from app.risk import assess_risk, compute_risk_score

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


def test_far_from_any_zone_adds_nothing():
    track = make_track(classification=Classification.DRONE)
    assert compute_risk_score(track, [], nearest_restricted_zone_distance_m=10_000.0) == 4


def test_within_500m_of_a_zone_adds_one():
    track = make_track(classification=Classification.DRONE)
    assert compute_risk_score(track, [], nearest_restricted_zone_distance_m=300.0) == 4 + 1


def test_within_100m_of_a_zone_adds_two():
    track = make_track(classification=Classification.DRONE)
    assert compute_risk_score(track, [], nearest_restricted_zone_distance_m=50.0) == 4 + 2


def test_no_known_zone_distance_adds_nothing():
    track = make_track(classification=Classification.DRONE)
    assert compute_risk_score(track, [], nearest_restricted_zone_distance_m=None) == 4


def test_score_is_capped_at_ten_even_when_every_factor_maxes_out():
    track = make_track(classification=Classification.DRONE, verified=True)
    score = compute_risk_score(
        track, [make_incident(IncidentSeverity.CRITICAL)], nearest_restricted_zone_distance_m=10.0
    )
    assert score == 10  # 4 + 2 + 4 + 2 = 12, capped


def test_assess_risk_lists_one_factor_per_nonzero_contribution():
    track = make_track(classification=Classification.DRONE, verified=True)
    result = assess_risk(
        track, [make_incident(IncidentSeverity.HIGH)], nearest_restricted_zone_distance_m=50.0
    )
    assert result.score == 10  # 4 + 2 + 3 + 2 = 11, capped
    assert len(result.factors) == 4
    assert any("drone" in f.lower() for f in result.factors)
    assert any("verified" in f.lower() for f in result.factors)
    assert any("high" in f.lower() and "incident" in f.lower() for f in result.factors)
    assert any("50m" in f for f in result.factors)


def test_assess_risk_zero_score_names_no_factors_present():
    track = make_track(classification=Classification.FRIENDLY)
    result = assess_risk(track, [])
    assert result.score == 0
    assert result.factors == ["No risk factors currently present"]


def test_assess_risk_ignored_track_names_that_as_the_only_factor():
    track = make_track(classification=Classification.DRONE, verified=True, ignored=True)
    result = assess_risk(track, [make_incident(IncidentSeverity.CRITICAL)])
    assert result.score == 0
    assert result.factors == ["Ignored by an operator"]
