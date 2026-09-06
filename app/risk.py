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


def compute_risk_score(track: Track, open_incidents: list[Incident]) -> int:
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
    return score
