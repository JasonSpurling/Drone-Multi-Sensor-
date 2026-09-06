"""Track risk scoring -- NOT a proprietary ML model or a claim of one:
a plain, fully-documented point score composed entirely from data this
app already computes for other reasons (classification, multi-sensor
verification, and any currently open incident's severity). "Why is this
track ranked here" is always answerable by reading three existing
fields, never a black box.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import Classification, Incident, IncidentSeverity, Track

# How urgent each classification is on its own, before any corroboration
# or incident evidence -- a confirmed/likely drone is what this app exists
# to flag; a friendly track should never compete for attention at all.
CLASSIFICATION_RISK = {
    Classification.DRONE: 4,
    Classification.UNKNOWN: 2,
    Classification.AIRCRAFT: 1,
    Classification.BIRD: 0,
    Classification.FRIENDLY: 0,
}

# Multiple independent sensor types agreeing is itself a reason to weight
# a track higher -- the same corroboration app.fusion/app.incidents
# already use for Verified/Unverified and severity escalation.
VERIFIED_BONUS = 2

# The worst open (non-resolved) incident already covering this track --
# reuses app.incidents' own severity levels rather than inventing a
# second scale that could disagree with the Alerts panel.
INCIDENT_SEVERITY_RISK = {
    IncidentSeverity.CRITICAL: 4,
    IncidentSeverity.HIGH: 3,
    IncidentSeverity.MEDIUM: 2,
    IncidentSeverity.LOW: 1,
}

# A track already inside a restricted zone (distance 0) almost certainly
# already has an open zone-incursion incident contributing the bonus
# above -- this tier instead catches a track that's *closing in* on one
# before it ever actually enters, which app.incidents has no signal for
# at all today (it only fires once a track is inside). Deliberately
# coarse, discrete tiers rather than a precise inverse-distance formula:
# a track either warrants a nudge of attention at this range or it
# doesn't, and two tiers are honestly defensible where a continuous
# curve implied a precision this app doesn't have.
_ZONE_PROXIMITY_TIERS_M = (
    (100.0, 2),  # within 100m of a protected zone's centroid
    (500.0, 1),  # within 500m
)


def _zone_proximity_risk(distance_m: float | None) -> int:
    if distance_m is None:
        return 0
    for threshold_m, points in _ZONE_PROXIMITY_TIERS_M:
        if distance_m <= threshold_m:
            return points
    return 0


def is_approaching_zone(distance_m: float | None) -> bool:
    """True within the same proximity range _zone_proximity_risk rewards
    -- the single shared definition of "close enough to a protected zone
    to be worth calling out," used for Track.zone_status's 'approaching'
    value as well as the risk score itself, so the two never disagree.
    """
    return _zone_proximity_risk(distance_m) > 0


@dataclass(frozen=True)
class RiskAssessment:
    score: int
    # Plain-English reasons, one per factor that actually contributed
    # (never a zero-point factor) -- the "why is this ranked here" a risk-
    # explanation panel needs, computed alongside the score itself rather
    # than a second function that could quietly drift out of sync with it.
    factors: list[str]


def assess_risk(
    track: Track, open_incidents: list[Incident], nearest_restricted_zone_distance_m: float | None = None
) -> RiskAssessment:
    """The single source of truth for both Track.risk_score and
    Track.risk_factors -- see compute_risk_score below for the score-only
    convenience wrapper most callers actually want.

    An operator's Ignore action (Track.ignored) always scores 0 -- they've
    deliberately said this track shouldn't compete for attention, so the
    score reflects that outright rather than just quietly ranking lower.
    """
    if track.ignored:
        return RiskAssessment(score=0, factors=["Ignored by an operator"])

    factors: list[str] = []
    score = 0

    classification_points = CLASSIFICATION_RISK.get(track.classification, 0)
    if classification_points:
        factors.append(f"Classified as {track.classification.value} (+{classification_points})")
        score += classification_points

    if track.verified:
        source_count = track.corroborating_sensor_types or 2
        factors.append(f"Verified by {source_count}+ independent sensor types (+{VERIFIED_BONUS})")
        score += VERIFIED_BONUS

    if open_incidents:
        worst = max(open_incidents, key=lambda i: INCIDENT_SEVERITY_RISK.get(i.severity, 0))
        incident_points = INCIDENT_SEVERITY_RISK.get(worst.severity, 0)
        if incident_points:
            incident_label = worst.incident_type.value.replace("_", " ")
            factors.append(f"Open {worst.severity.value}-severity {incident_label} incident (+{incident_points})")
            score += incident_points

    proximity_points = _zone_proximity_risk(nearest_restricted_zone_distance_m)
    if proximity_points and nearest_restricted_zone_distance_m is not None:
        distance = round(nearest_restricted_zone_distance_m)
        factors.append(f"Within {distance}m of a protected zone (+{proximity_points})")
        score += proximity_points

    if not factors:
        factors.append("No risk factors currently present")

    # Capped at the advertised 0-10 range -- classification + verified +
    # incident + proximity can otherwise sum past it for a track that's
    # every kind of urgent at once; the cap only ever compresses an
    # already-maximal case, never changes the ranking between two tracks
    # below it, and never removes a factor from the explanation above.
    return RiskAssessment(score=min(score, 10), factors=factors)


def compute_risk_score(
    track: Track, open_incidents: list[Incident], nearest_restricted_zone_distance_m: float | None = None
) -> int:
    """0-10 point score, higher = more urgent for an operator to look at.
    See assess_risk above for the same score plus a factor-by-factor
    explanation.
    """
    return assess_risk(track, open_incidents, nearest_restricted_zone_distance_m).score
