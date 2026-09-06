"""Track risk scoring -- NOT a proprietary ML model or a claim of one:
a plain, fully-documented point score composed entirely from data this
app already computes for other reasons (classification, multi-sensor
verification, and any currently open incident's severity). "Why is this
track ranked here" is always answerable by reading three existing
fields, never a black box.
"""

from __future__ import annotations

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


def compute_risk_score(
    track: Track, open_incidents: list[Incident], nearest_restricted_zone_distance_m: float | None = None
) -> int:
    """0-10 point score, higher = more urgent for an operator to look at.

    An operator's Ignore action (Track.ignored) always scores 0 -- they've
    deliberately said this track shouldn't compete for attention, so the
    score reflects that outright rather than just quietly ranking lower.
    """
    if track.ignored:
        return 0
    score = CLASSIFICATION_RISK.get(track.classification, 0)
    if track.verified:
        score += VERIFIED_BONUS
    if open_incidents:
        score += max(INCIDENT_SEVERITY_RISK.get(i.severity, 0) for i in open_incidents)
    score += _zone_proximity_risk(nearest_restricted_zone_distance_m)
    # Capped at the advertised 0-10 range (see Track.risk_score's
    # docstring) -- classification + verified + incident + proximity can
    # otherwise sum past it for a track that's every kind of urgent at
    # once; the cap only ever compresses an already-maximal case, never
    # changes the ranking between two tracks below it.
    return min(score, 10)
