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
    FUSION_HISTORY_LIMIT,
    INCIDENT_CORROBORATION_MIN_SENSOR_TYPES,
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
    list_open_incidents_for_track,
    list_recent_detections,
    update_incident,
)
from app.geo import local_m_to_latlon
from app.live import publish as publish_live_event
from app.metrics import incidents_opened_total
from app.mitigation import notify_mitigation_system
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
from app.notifications import notify_incident
from app.queue_publisher import publish_incident
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

_SEVERITY_ESCALATION = {
    IncidentSeverity.LOW: IncidentSeverity.MEDIUM,
    IncidentSeverity.MEDIUM: IncidentSeverity.HIGH,
    IncidentSeverity.HIGH: IncidentSeverity.CRITICAL,
    IncidentSeverity.CRITICAL: IncidentSeverity.CRITICAL,
}


def _corroborating_sensor_type_count(track: Track) -> int:
    """Distinct sensor types among the same recent-detection window
    app.fusion's classification fusion itself considers
    (FUSION_HISTORY_LIMIT) -- multiple sensor *types* independently
    reporting on this track, not just multiple detections from the same
    one repeating itself.
    """
    if track.id is None or track.site_id is None:
        return 0
    history = list_recent_detections(track.id, track.site_id, FUSION_HISTORY_LIMIT)
    return len({d.sensor_type for d in history})


def _severity_with_corroboration(
    track: Track, base_severity: IncidentSeverity
) -> tuple[IncidentSeverity, int]:
    """Escalates `base_severity` one level once at least
    INCIDENT_CORROBORATION_MIN_SENSOR_TYPES distinct sensor types have
    reported on this track. app/fusion.py already fuses multi-sensor
    evidence into one classification label, but a DRONE reading
    independently confirmed by radar+RF+camera together is more
    actionable than the identical label from a single acoustic sensor
    alone -- severity previously couldn't tell those two situations
    apart, since it was keyed only on the resulting label. Returns the
    (possibly escalated) severity alongside the sensor-type count that
    was actually checked, so a caller that escalates can say why.
    """
    sensor_type_count = _corroborating_sensor_type_count(track)
    if sensor_type_count >= INCIDENT_CORROBORATION_MIN_SENSOR_TYPES:
        return _SEVERITY_ESCALATION[base_severity], sensor_type_count
    return base_severity, sensor_type_count


def _open_incident(
    track: Track, zone: Zone, incident_type: IncidentType, description: str
) -> Incident | None:
    # Both callers (check_zone_incidents, check_predicted_incursions) only
    # ever call this with a persisted track/zone -- checked before the call
    # in each, since a None id can't be looked up or referenced by a
    # foreign key anyway.
    assert track.id is not None and zone.id is not None and track.site_id is not None
    if get_open_incident(track.id, zone.id, incident_type.value, track.site_id) is not None:
        return None
    base_severity = _SEVERITY_BY_CLASSIFICATION.get(track.classification, IncidentSeverity.MEDIUM)
    severity, sensor_type_count = _severity_with_corroboration(track, base_severity)
    if severity != base_severity:
        description = f"{description} (escalated: corroborated by {sensor_type_count} sensor types)"
    incident = create_incident(
        Incident(
            site_id=track.site_id,
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
    notify_mitigation_system(incident, track)
    publish_incident(incident.model_dump(mode="json"))
    publish_live_event(track.site_id, {"type": "incident_opened", "incident": incident.model_dump(mode="json")})
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


def _open_behavioral_incident(
    track: Track, incident_type: IncidentType, description: str, *, related_track_id: int | None = None
) -> Incident | None:
    if track.id is None or track.site_id is None:
        return None
    if get_open_behavioral_incident(
        track.id, incident_type.value, track.site_id, related_track_id=related_track_id
    ) is not None:
        return None

    base_severity = _BEHAVIORAL_SEVERITY.get(incident_type, IncidentSeverity.MEDIUM)
    severity, sensor_type_count = _severity_with_corroboration(track, base_severity)
    if severity != base_severity:
        description = f"{description} (escalated: corroborated by {sensor_type_count} sensor types)"
    incident = create_incident(
        Incident(
            site_id=track.site_id,
            incident_uid=str(uuid.uuid4()),
            incident_type=incident_type,
            severity=severity,
            status=IncidentStatus.OPEN,
            track_id=track.id,
            zone_id=None,
            related_track_id=related_track_id,
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
    notify_mitigation_system(incident, track)
    publish_incident(incident.model_dump(mode="json"))
    publish_live_event(track.site_id, {"type": "incident_opened", "incident": incident.model_dump(mode="json")})
    return incident


def check_loitering_incident(track: Track) -> Incident | None:
    """Checked per-track on every update (unlike formation/shadowing,
    which compare multiple tracks and run on a periodic sweep instead --
    see app/behavior_sweep.py): does this track's own recent history show
    it circling/hovering in one place rather than transiting through?
    """
    if track.id is None or track.site_id is None:
        return None
    history = list_recent_detections(track.id, track.site_id, 500)
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
    valid_tracks = [t for t in tracks if t.id is not None and t.site_id is not None]
    # Each track's history is independent of which pair it's being compared
    # against, so fetch it once per track rather than once per pair --
    # avoids O(n^2) redundant DB reads of the same track's (up to 500-row)
    # history as the active-track count grows.
    histories: dict[int, list[Detection]] = {}
    for t in valid_tracks:
        assert t.id is not None and t.site_id is not None  # guaranteed by the valid_tracks filter above
        histories[t.id] = list_recent_detections(t.id, t.site_id, 500)
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
                assert other.id is not None  # from valid_tracks
                incident = _open_behavioral_incident(
                    track, IncidentType.SHADOWING,
                    f"Track {track.track_uid} has maintained close proximity to track {other.track_uid}",
                    related_track_id=other.id,
                )
                if incident is not None:
                    opened.append(incident)
    return opened


def check_zone_incidents(track: Track) -> list[Incident]:
    """Open a zone-incursion incident for each restricted zone a track's
    current position falls inside, unless one is already open for that
    track/zone pair.
    """
    if track.id is None or track.site_id is None or track.latitude is None or track.longitude is None:
        return []

    opened: list[Incident] = []
    for zone in zones_containing_point(track.latitude, track.longitude, track.site_id, track.altitude_m):
        if zone.zone_type != ZoneType.RESTRICTED or zone.id is None:
            continue
        incident = _open_incident(
            track, zone, IncidentType.ZONE_INCURSION,
            f"Track {track.track_uid} entered restricted zone '{zone.name}'",
        )
        if incident is not None:
            opened.append(incident)
    return opened


def _auto_close_incident(incident: Incident, reason: str) -> Incident:
    """Closes an incident the system itself determined no longer applies
    -- distinct from POST /api/incidents/{id}/resolve (app/api/
    incidents.py), which is an operator's deliberate "we reviewed this
    and it's handled" judgment call. This never fabricates that judgment:
    `acknowledged_by` is left exactly as it was (None if no operator ever
    acknowledged it), and `reason` is appended to the description so an
    after-action report or audit reader can tell the two apart -- an
    incident nobody ever looked at reads differently from one an operator
    actively closed.
    """
    incident.status = IncidentStatus.RESOLVED
    incident.closed_at = utcnow()
    note = f"(auto-closed: {reason})"
    incident.description = f"{incident.description} {note}" if incident.description else note
    updated = update_incident(incident)
    logger.info(
        "Incident auto-closed (%s): track %s, incident %s", reason, incident.track_id, incident.incident_uid
    )
    if incident.site_id is not None:
        publish_live_event(
            incident.site_id, {"type": "incident_resolved", "incident": updated.model_dump(mode="json")}
        )
    return updated


def check_zone_incident_resolutions(track: Track) -> list[Incident]:
    """The other half of check_zone_incidents(): that function only ever
    opens a ZONE_INCURSION incident when a track's position enters a
    restricted zone -- nothing closed one back out once the track's
    position left again, so an incident for a track that flew straight
    through a zone in seconds stayed open (or acknowledged) indefinitely,
    long after the track itself was gone. Runs on every detection commit
    alongside check_zone_incidents, so an incident closes as soon as a
    new position confirms the track is no longer inside that zone.
    """
    if track.id is None or track.site_id is None or track.latitude is None or track.longitude is None:
        return []

    still_inside_zone_ids = {
        zone.id
        for zone in zones_containing_point(track.latitude, track.longitude, track.site_id, track.altitude_m)
        if zone.zone_type == ZoneType.RESTRICTED
    }
    closed = []
    for incident in list_open_incidents_for_track(track.id, track.site_id, IncidentType.ZONE_INCURSION.value):
        if incident.zone_id is not None and incident.zone_id not in still_inside_zone_ids:
            closed.append(_auto_close_incident(incident, "track exited the zone"))
    return closed


def close_incidents_for_closed_track(track: Track) -> list[Incident]:
    """Called from app.tracking.expire_stale_tracks when a track transitions
    to CLOSED: nothing was tracking that object's position/behavior once
    it went stale, so no still-open incident (zone incursion, predicted
    incursion, loitering, formation, shadowing -- every type, not just
    zone-based ones) has anything left to keep monitoring either. Without
    this, a track that simply flew out of sensor range -- rather than
    being resolved by check_zone_incident_resolutions' position check --
    left its incidents open forever with no path to close them at all.
    """
    if track.id is None or track.site_id is None:
        return []
    return [
        _auto_close_incident(incident, "track closed")
        for incident in list_open_incidents_for_track(track.id, track.site_id)
    ]


def check_predicted_incursions(track: Track) -> list[Incident]:
    """Project a track's position forward by PREDICTIVE_HORIZON_SECONDS
    using its Kalman-filtered velocity, and open an early-warning incident
    for any restricted zone the projection enters that the track isn't
    already inside (that's check_zone_incidents' job).
    """
    if (
        track.id is None
        or track.site_id is None
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
        zone.id
        for zone in zones_containing_point(track.latitude, track.longitude, track.site_id, track.altitude_m)
    }

    opened: list[Incident] = []
    for zone in zones_containing_point(projected_lat, projected_lon, track.site_id, track.altitude_m):
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
