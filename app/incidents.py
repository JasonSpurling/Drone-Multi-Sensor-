"""Incident creation from zone-membership rules, both for a track's actual
current position and its Kalman-projected future position.
"""

from __future__ import annotations

import logging
import math
import uuid

from app.alerting import notify_escalations
from app.behavior import detect_formations, detect_loitering, detect_shadowing
from app.config import (
    FORMATION_HEADING_TOLERANCE_DEG,
    FORMATION_MAX_SPACING_M,
    FORMATION_SPEED_TOLERANCE_MPS,
    LOITERING_MIN_DURATION_S,
    LOITERING_RADIUS_M,
    PREDICTIVE_HORIZON_SECONDS,
    SHADOWING_MAX_DISTANCE_M,
    SHADOWING_MIN_DURATION_S,
)
from app.db import (
    create_incident,
    get_open_behavioral_incident,
    get_open_incident,
    list_recent_detections,
)
from app.geo import local_m_to_latlon
from app.metrics import incidents_opened_total
from app.notifications import notify_incident
from app.queue_publisher import publish_incident
from app.models import (
    Classification,
    Detection,
    Incident,
    IncidentSeverity,
    IncidentStatus,
    IncidentType,
    Track,
    Zone,
    ZoneType,
)
from app.util import utcnow
from app.zones import zones_containing_point

logger = logging.getLogger(__name__)

_SEVERITY_BY_CLASSIFICATION = {
    Classification.DRONE: IncidentSeverity.HIGH,
    Classification.UNKNOWN: IncidentSeverity.MEDIUM,
    Classification.AIRCRAFT: IncidentSeverity.LOW,
    Classification.BIRD: IncidentSeverity.LOW,
    # Deliberately MEDIUM, not LOW: unlike AIRCRAFT (a cooperative ADS-B
    # transponder signal) or BIRD (an independent sensor-confidence
    # reading), FRIENDLY comes from an unauthenticated, unsigned
    # raw_data.operator_id claim in the detection payload itself (see
    # app/allowlist.py) -- an attacker with only ingest access can assert
    # any operator_id they know or guess. Tempering the severity is
    # reasonable; fully suppressing it to LOW on an unverified self-
    # report is not, until a cryptographically verified identity channel
    # exists.
    Classification.FRIENDLY: IncidentSeverity.MEDIUM,
}

# A track's estimated speed below this is treated as noise around zero
# rather than genuine motion, so it doesn't drive a spurious predicted
# projection off in a random direction.
_MIN_SPEED_FOR_PROJECTION_MPS = 1.0


def _open_incident(
    track: Track, zone: Zone, incident_type: IncidentType, description: str
) -> Incident | None:
    # Both callers (check_zone_incidents, check_predicted_incursions) only
    # ever call this with a persisted track/zone -- checked before the call
    # in each, since a None id can't be looked up or referenced by a
    # foreign key anyway.
    assert track.id is not None and zone.id is not None
    if get_open_incident(track.id, zone.id, incident_type.value) is not None:
        return None
    severity = _SEVERITY_BY_CLASSIFICATION.get(track.classification, IncidentSeverity.MEDIUM)
    incident = create_incident(
        Incident(
            incident_uid=str(uuid.uuid4()),
            incident_type=incident_type,
            severity=severity,
            status=IncidentStatus.OPEN,
            track_id=track.id,
            zone_id=zone.id,
            opened_at=utcnow(),
            description=description,
        )
    )
    logger.warning(
        "Incident opened (%s): track %s, zone '%s' (severity=%s)",
        incident_type.value, track.track_uid, zone.name, severity.value,
    )
    incidents_opened_total.labels(incident_type=incident_type.value, severity=severity.value).inc()
    notify_incident(incident)
    notify_escalations(incident)
    publish_incident(incident.model_dump(mode="json"))
    return incident


# Behavioral incidents (app/behavior.py) aren't tied to a zone, unlike
# zone-based severity (which reflects how trusted a track's own
# classification is), so severity here reflects how concerning the
# BEHAVIOR itself is: coordinated formation flight and sustained
# shadowing of another track are both more deliberate/concerning patterns
# than loitering, which has innocuous explanations (a camera drone
# filming one spot, a delivery drone waiting for a door to open) as often
# as concerning ones.
_BEHAVIORAL_SEVERITY = {
    IncidentType.LOITERING: IncidentSeverity.MEDIUM,
    IncidentType.FORMATION: IncidentSeverity.HIGH,
    IncidentType.SHADOWING: IncidentSeverity.HIGH,
}


def _open_behavioral_incident(track: Track, incident_type: IncidentType, description: str) -> Incident | None:
    if track.id is None:
        return None
    if get_open_behavioral_incident(track.id, incident_type.value) is not None:
        return None

    severity = _BEHAVIORAL_SEVERITY.get(incident_type, IncidentSeverity.MEDIUM)
    incident = create_incident(
        Incident(
            incident_uid=str(uuid.uuid4()),
            incident_type=incident_type,
            severity=severity,
            status=IncidentStatus.OPEN,
            track_id=track.id,
            zone_id=None,
            opened_at=utcnow(),
            description=description,
        )
    )
    logger.warning(
        "Incident opened (%s): track %s (severity=%s)", incident_type.value, track.track_uid, severity.value,
    )
    incidents_opened_total.labels(incident_type=incident_type.value, severity=severity.value).inc()
    notify_incident(incident)
    notify_escalations(incident)
    publish_incident(incident.model_dump(mode="json"))
    return incident


def check_loitering_incident(track: Track) -> Incident | None:
    """Checked per-track on every update (unlike formation/shadowing,
    which compare multiple tracks and run on a periodic sweep instead --
    see app/behavior_sweep.py): does this track's own recent history show
    it circling/hovering in one place rather than transiting through?
    """
    if track.id is None:
        return None
    history = list_recent_detections(track.id, 500)
    if not detect_loitering(history, radius_m=LOITERING_RADIUS_M, min_duration_s=LOITERING_MIN_DURATION_S):
        return None
    return _open_behavioral_incident(
        track, IncidentType.LOITERING,
        f"Track {track.track_uid} has been loitering (within ~{LOITERING_RADIUS_M:.0f}m) "
        f"for over {LOITERING_MIN_DURATION_S:.0f}s",
    )


def check_formation_incidents(tracks: list[Track]) -> list[Incident]:
    """Cross-track sweep: groups of active tracks moving together in a
    coordinated formation -- the pattern a swarm shows. Opens one
    incident per track in each detected formation (this app's Incident
    model references a single track_id, not a group), each describing
    the whole group.
    """
    formations = detect_formations(
        tracks,
        max_spacing_m=FORMATION_MAX_SPACING_M,
        heading_tolerance_deg=FORMATION_HEADING_TOLERANCE_DEG,
        speed_tolerance_mps=FORMATION_SPEED_TOLERANCE_MPS,
    )
    tracks_by_id = {track.id: track for track in tracks}
    opened: list[Incident] = []
    for group in formations:
        group_uids = ", ".join(tracks_by_id[tid].track_uid for tid in group if tid in tracks_by_id)
        for track_id in group:
            track = tracks_by_id.get(track_id)
            if track is None:
                continue
            incident = _open_behavioral_incident(
                track, IncidentType.FORMATION,
                f"Track {track.track_uid} moving in formation with: {group_uids}",
            )
            if incident is not None:
                opened.append(incident)
    return opened


def check_shadowing_incidents(tracks: list[Track]) -> list[Incident]:
    """Cross-track sweep: pairs of active tracks where one has
    consistently stayed close to the other -- sustained escort/shadowing,
    not a brief pass. Opens an incident for both tracks in a shadowing
    pair. O(n^2) track-history comparisons -- fine for the track counts a
    single-site deployment actually sees; a very high-track-count
    deployment would want to narrow candidate pairs by current proximity
    first rather than comparing every active pair's full history.
    """
    opened: list[Incident] = []
    valid_tracks = [t for t in tracks if t.id is not None]
    # Each track's history is independent of which pair it's being compared
    # against, so fetch it once per track rather than once per pair --
    # avoids O(n^2) redundant DB reads of the same track's (up to 500-row)
    # history as the active-track count grows.
    histories: dict[int, list[Detection]] = {}
    for t in valid_tracks:
        assert t.id is not None  # guaranteed by the valid_tracks filter above
        histories[t.id] = list_recent_detections(t.id, 500)
    for i, track_a in enumerate(valid_tracks):
        for track_b in valid_tracks[i + 1 :]:
            assert track_a.id is not None and track_b.id is not None  # from valid_tracks
            if not detect_shadowing(
                histories[track_a.id], histories[track_b.id],
                max_distance_m=SHADOWING_MAX_DISTANCE_M, min_duration_s=SHADOWING_MIN_DURATION_S,
            ):
                continue
            for track in (track_a, track_b):
                other = track_b if track is track_a else track_a
                incident = _open_behavioral_incident(
                    track, IncidentType.SHADOWING,
                    f"Track {track.track_uid} has maintained close proximity to track {other.track_uid}",
                )
                if incident is not None:
                    opened.append(incident)
    return opened


def check_zone_incidents(track: Track) -> list[Incident]:
    """Open a zone-incursion incident for each restricted zone a track's
    current position falls inside, unless one is already open for that
    track/zone pair.
    """
    if track.id is None or track.latitude is None or track.longitude is None:
        return []

    opened: list[Incident] = []
    for zone in zones_containing_point(track.latitude, track.longitude, track.altitude_m):
        if zone.zone_type != ZoneType.RESTRICTED or zone.id is None:
            continue
        incident = _open_incident(
            track, zone, IncidentType.ZONE_INCURSION,
            f"Track {track.track_uid} entered restricted zone '{zone.name}'",
        )
        if incident is not None:
            opened.append(incident)
    return opened


def check_predicted_incursions(track: Track) -> list[Incident]:
    """Project a track's position forward by PREDICTIVE_HORIZON_SECONDS
    using its Kalman-filtered velocity, and open an early-warning incident
    for any restricted zone the projection enters that the track isn't
    already inside (that's check_zone_incidents' job).
    """
    if (
        track.id is None
        or track.latitude is None
        or track.longitude is None
        or track.speed_mps is None
        or track.heading_deg is None
        or track.speed_mps < _MIN_SPEED_FOR_PROJECTION_MPS
    ):
        return []

    heading_rad = math.radians(track.heading_deg)
    east_m = track.speed_mps * math.sin(heading_rad) * PREDICTIVE_HORIZON_SECONDS
    north_m = track.speed_mps * math.cos(heading_rad) * PREDICTIVE_HORIZON_SECONDS
    projected_lat, projected_lon = local_m_to_latlon(east_m, north_m, track.latitude, track.longitude)

    current_zone_ids = {
        zone.id for zone in zones_containing_point(track.latitude, track.longitude, track.altitude_m)
    }

    opened: list[Incident] = []
    for zone in zones_containing_point(projected_lat, projected_lon, track.altitude_m):
        if zone.zone_type != ZoneType.RESTRICTED or zone.id is None or zone.id in current_zone_ids:
            continue
        incident = _open_incident(
            track, zone, IncidentType.PREDICTED_INCURSION,
            f"Track {track.track_uid} projected to enter restricted zone '{zone.name}' "
            f"within {PREDICTIVE_HORIZON_SECONDS:.0f}s",
        )
        if incident is not None:
            opened.append(incident)
    return opened
