"""Domain data models for the drone multi-sensor system."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.util import utcnow


class SensorType(StrEnum):
    RADAR = "radar"
    RF = "rf"
    ACOUSTIC = "acoustic"
    CAMERA = "camera"
    ADSB = "adsb"
    OTHER = "other"


class SensorStatus(StrEnum):
    ONLINE = "online"
    STALE = "stale"
    OFFLINE = "offline"
    # Registered (app/api/sensor_registry.py -- a known mounting position
    # exists) but has never actually posted a single detection -- distinct
    # from OFFLINE, which means "was seen before, has since gone quiet."
    # Without this status, an operator has no way to tell "the sensor
    # nobody bothered to hook up yet" apart from "the sensor that just
    # went down" -- both looked identical (simply absent from
    # GET /api/sensors) before this.
    MISSING = "missing"


class TrackStatus(StrEnum):
    ACTIVE = "active"
    LOST = "lost"
    CLOSED = "closed"


class Classification(StrEnum):
    UNKNOWN = "unknown"
    DRONE = "drone"
    BIRD = "bird"
    AIRCRAFT = "aircraft"
    FRIENDLY = "friendly"


class TrainableLabel(StrEnum):
    """The subset of Classification an operator can assign as ground truth
    for ML training (app/ml/train.py) -- deliberately excludes FRIENDLY,
    same reasoning as app.ml.train's own docstring: FRIENDLY comes from a
    cryptographically verified authorized-operator signature
    (app/allowlist.py), not a feature to train a classifier on, so it's
    not a label a human ever assigns here either.
    """

    UNKNOWN = "unknown"
    DRONE = "drone"
    BIRD = "bird"
    AIRCRAFT = "aircraft"


class IncidentType(StrEnum):
    ZONE_INCURSION = "zone_incursion"
    PREDICTED_INCURSION = "predicted_incursion"
    UNAUTHORIZED_FLIGHT = "unauthorized_flight"
    LOSS_OF_TRACK = "loss_of_track"
    SENSOR_FAULT = "sensor_fault"
    LOITERING = "loitering"
    FORMATION = "formation"
    SHADOWING = "shadowing"
    OTHER = "other"


class IncidentSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class ZoneType(StrEnum):
    RESTRICTED = "restricted"
    NO_FLY = "no_fly"
    MONITORING = "monitoring"
    SAFE = "safe"


class Site(BaseModel):
    """A physical site/campus this deployment monitors. Every row-level
    record (Track, Detection, Incident, Zone, SensorRegistration,
    AuthorizedOperator) belongs to exactly one Site -- see app/sites.py
    and app/auth.py's Principal.site_id for how a request's site is
    determined (from its API key, not client-supplied).
    """

    id: int | None = None
    name: str = Field(min_length=1, max_length=200)


class Detection(BaseModel):
    """A single raw detection reported by one sensor."""

    id: int | None = None
    # Set server-side from the authenticated request's Principal.site_id
    # (see app/auth.py) -- never client-supplied, the same reasoning as
    # georeferenced below: a client claiming a site it doesn't hold a key
    # for would let it write into another site's data.
    site_id: int | None = None
    sensor_id: str = Field(min_length=1, max_length=100)
    sensor_type: SensorType
    timestamp: datetime = Field(default_factory=utcnow)
    track_id: int | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    altitude_m: float | None = None
    azimuth_deg: float | None = None
    range_m: float | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    raw_data: dict | None = None
    georeferenced: bool = Field(
        default=False,
        description="Set server-side by app/georeference.py when latitude/longitude were computed "
        "from this sensor's azimuth/range report rather than reported directly. Any client-supplied "
        "value is discarded on ingest (see app/api/detections.py) -- a signed detection's signature "
        "must verify against what the sensor actually signed, not a claim the client controls.",
    )
    human_label: TrainableLabel | None = Field(
        default=None,
        description="An operator's ground-truth label for this detection (PUT "
        "/api/detections/{id}/label), independent of and never overwritten by track.classification "
        "(the system's own fused/ML-assisted best guess). Building up a set of these is what turns "
        "GET /api/ml/training-data/export from empty into something app.ml.train can actually learn "
        "from -- see app/ml/__init__.py for why this repo ships no such data itself.",
    )


class DetectionLabelInput(BaseModel):
    """Body for PUT /api/detections/{id}/label. label=None clears a
    previously-set human_label (e.g. correcting a mis-click) rather than
    only ever being able to set one.
    """

    label: TrainableLabel | None = None


class Track(BaseModel):
    """A fused sequence of detections believed to be the same object."""

    id: int | None = None
    site_id: int | None = None
    track_uid: str
    first_seen: datetime
    last_seen: datetime
    status: TrackStatus = TrackStatus.ACTIVE
    classification: Classification = Classification.UNKNOWN
    latitude: float | None = None
    longitude: float | None = None
    altitude_m: float | None = None
    heading_deg: float | None = Field(
        default=None, description="Kalman-filtered heading, degrees clockwise from true north"
    )
    speed_mps: float | None = Field(default=None, description="Kalman-filtered ground speed")
    position_uncertainty_m: float | None = Field(
        default=None, description="1-sigma radial position uncertainty from the Kalman filter"
    )
    maneuver_probability: float | None = Field(
        default=None,
        description="IMM MANEUVER-mode probability (0-1): how confident the tracker is that "
        "this object is currently maneuvering (turning/accelerating) rather than flying straight",
    )
    aircraft_category: str | None = Field(
        default=None,
        max_length=2,
        description="The real ICAO ADS-B 'emitter category' code (DO-260B Table 2-36) this track's "
        "most recent category-reporting detection carried, e.g. 'A7' (rotorcraft), 'B1' (glider), "
        "'B2' (lighter-than-air), 'B6' (unmanned aerial vehicle), 'A1'/'A2' (light/small fixed-wing), "
        "'A3'-'A6' (large/heavy/high-performance fixed-wing) -- see app/adapters/dump1090_bridge.py "
        "for where this is actually decoded from a live ADS-B feed. None whenever no detection has "
        "reported one (most sensors don't carry this at all), in which case the dashboard falls back "
        "to a generic aircraft glyph rather than guessing.",
    )
    classification_confidence: float | None = Field(
        default=None,
        description="How strongly current evidence backs this track's stored `classification` "
        "specifically (0-1) -- see app.fusion.classification_confidence. Distinct from the "
        "classification label itself, which app.tracking's upgrade-only ratchet never downgrades "
        "(never re-flagging a real drone as a bird is the failure mode that avoids); this can "
        "still fall as contradicting evidence accumulates or the track goes stale, so a DRONE "
        "track nobody's heard from in a while reads honestly as low-confidence DRONE rather than "
        "either silently downgrading or looking exactly as certain as a freshly-confirmed one. "
        "GET /api/tracks applies read-time staleness decay (app.fusion.decay_classification_confidence) "
        "on top of the as-of-last-detection value stored here. None for an UNKNOWN track -- there's "
        "no meaningful confidence in not knowing.",
    )
    corroborating_sensor_types: int | None = Field(
        default=None,
        description="How many distinct sensor types have contributed a detection to this track's "
        "recent history (app.fusion.corroborating_sensor_type_count's corroboration window) -- "
        "computed fresh by GET /api/tracks/GET /api/tracks/{id}, not stored. Not populated on a "
        "Track built any other way (e.g. inside app.tracking's own commit path), since it's a "
        "read-time-only view, not part of a track's actual persisted state.",
    )
    verified: bool | None = Field(
        default=None,
        description="corroborating_sensor_types >= INCIDENT_CORROBORATION_MIN_SENSOR_TYPES -- the "
        "same corroboration threshold app.incidents already uses to escalate incident severity, "
        "not a second, differently-tuned definition of 'verified' invented just for this field. "
        "The dashboard's Verified/Unverified track grouping is exactly this.",
    )
    contributing_sensor_types: list[str] | None = Field(
        default=None,
        description="Which distinct sensor types (the same recent-detection window "
        "app.fusion.corroborating_sensor_type_count considers) have actually contributed a "
        "detection to this track -- e.g. ['radar', 'camera']. Read-time-only, same as "
        "corroborating_sensor_types/verified above (its len() is that count); not populated on "
        "a Track built any other way.",
    )
    risk_score: int | None = Field(
        default=None,
        description="app.risk.compute_risk_score's 0-10 point score -- how urgent this track is to "
        "look at, composed entirely from classification + verified + the worst open incident's "
        "severity, all already-shown fields, never a proprietary/opaque ML score this app has no "
        "trained model to actually back. Read-time-only, same as corroborating_sensor_types/verified "
        "above; 0 for an ignored track regardless of the rest.",
    )
    risk_factors: list[str] | None = Field(
        default=None,
        description="app.risk.assess_risk's plain-English reasons behind risk_score, one per factor "
        "that actually contributed (never a zero-point one) -- e.g. ['Classified as drone (+4)', "
        "'Within 80m of a protected zone (+1)']. Powers the dashboard's risk-explanation display; "
        "read-time-only, same as risk_score.",
    )
    zone_status: str | None = Field(
        default=None,
        description="This track's relationship to the nearest active restricted zone right now: "
        "'inside' (a real point-in-polygon containment, app.zones.zones_containing_point), "
        "'approaching' (outside, but within the same proximity range app.risk's zone-proximity bonus "
        "uses), or 'none'. Read-time-only, same as risk_score.",
    )
    ignored: bool = Field(
        default=False,
        description="An operator's deliberate 'stop alerting on this' suppression -- unlike "
        "`classification`, this is persisted (POST /api/tracks/{id}/ignore), not read-time-only. "
        "A zone/behavioral incident is never opened for an ignored track (app.incidents._open_incident "
        "and ._open_behavioral_incident both check it first); the track itself keeps updating and "
        "showing up everywhere else exactly as before -- ignoring never hides or deletes data, only "
        "suppresses new incidents from it. Reflects current effective state, not just the raw stored "
        "flag: app.db._row_to_track resolves an expired ignored_until back to False on every read, so "
        "this is never stale past whatever last actually loaded the row.",
    )
    ignored_until: datetime | None = Field(
        default=None,
        description="When this track's Ignore expires and reverts to normal alerting on its own -- "
        "None means either not ignored, or ignored with no expiry (an operator picked 'Indefinitely', "
        "meaning 'until I manually Unignore it'). Set via POST /api/tracks/{id}/ignore's "
        "duration_minutes.",
    )


class TrackClassificationInput(BaseModel):
    """Body for POST /api/tracks/{id}/classify -- a deliberate operator
    override ("that's our security team's drone", "confirmed hostile", or
    "never mind, forget that call and let automatic classification start
    fresh"), distinct from the automated fusion ratchet that normally sets
    Track.classification (see that field's own docstring). Restricted to
    FRIENDLY, DRONE, and UNKNOWN, not the full Classification enum: an
    operator watching the dashboard is making one of three calls -- this
    is ours (friendly), this is the thing we're watching for (confirmed
    drone), or clear my own earlier call and go back to unclassified (the
    "Neutral" action) -- never reclassifying a track as a bird or an
    aircraft, which is what the sensor evidence itself is for, not an
    operator's manual say-so. Mirrors the same "automatic system decision
    vs. a human's deliberate override" split already established between
    app.incidents' auto-close logic and the operator-driven POST
    /api/incidents/{id}/resolve.
    """

    classification: Literal[Classification.FRIENDLY, Classification.DRONE, Classification.UNKNOWN]


class TrackIgnoreInput(BaseModel):
    """Body for POST /api/tracks/{id}/ignore -- see Track.ignored's docstring.

    `duration_minutes` only matters when `ignored=True`: omitted or None means
    "Indefinitely" (until manually unignored); a number means the ignore
    expires and reverts to normal alerting on its own after that many
    minutes. Ignored when `ignored=False` -- unignoring always clears any
    expiry along with the flag itself.
    """

    ignored: bool
    duration_minutes: int | None = Field(default=None, gt=0)


class Incident(BaseModel):
    """An actionable event raised from track/zone analysis."""

    id: int | None = None
    site_id: int | None = None
    incident_uid: str
    incident_type: IncidentType
    severity: IncidentSeverity = IncidentSeverity.LOW
    status: IncidentStatus = IncidentStatus.OPEN
    track_id: int | None = None
    zone_id: int | None = None
    related_track_id: int | None = Field(
        default=None,
        description="For a SHADOWING incident, the other track being shadowed -- lets "
        "track_id shadowing two different tracks at once open a separate incident for "
        "each pair instead of the second shadow relationship being silently dropped as "
        "a duplicate of the first. Unused (always None) for every other incident type.",
    )
    opened_at: datetime = Field(default_factory=utcnow)
    closed_at: datetime | None = None
    description: str | None = None
    acknowledged_by: str | None = Field(
        default=None, description="Identity (API key name/role) that acknowledged this incident"
    )


class Zone(BaseModel):
    """A geofenced area of interest, e.g. a no-fly zone."""

    id: int | None = None
    site_id: int | None = None
    name: str
    zone_type: ZoneType
    polygon: list[tuple[float, float]] = Field(
        description="List of (latitude, longitude) vertices"
    )
    min_altitude_m: float | None = None
    max_altitude_m: float | None = None
    active: bool = True


class ZoneInput(BaseModel):
    """A zone as an admin creates/edits it via POST/PUT /api/zones -- same
    shape as Zone minus id/site_id, which are always server-assigned (see
    app/api/zones.py), the same reasoning as SensorRegistrationInput and
    AuthorizedOperatorInput above.
    """

    name: str = Field(min_length=1, max_length=200)
    zone_type: ZoneType
    polygon: list[tuple[float, float]] = Field(
        min_length=3, description="List of (latitude, longitude) vertices -- at least 3 to form a real polygon"
    )
    min_altitude_m: float | None = None
    max_altitude_m: float | None = None
    active: bool = True


class SensorHealth(BaseModel):
    """Derived liveness status for a sensor, based on its most recent
    detection -- except status=MISSING, which has no detection to derive
    from at all (last_seen is None in that case: never fabricated as a
    real timestamp).
    """

    sensor_id: str
    sensor_type: SensorType
    last_seen: datetime | None
    status: SensorStatus


class SensorRegistrationInput(BaseModel):
    """A sensor's fixed mounting position/orientation, used to georeference
    detections that report azimuth/range instead of lat/lon directly (see
    app/georeference.py).
    """

    sensor_type: SensorType
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude_m: float | None = None
    azimuth_reference_deg: float = Field(
        default=0.0,
        description="Compass bearing (degrees) the sensor's azimuth_deg=0 points to",
    )
    active: bool = True
    camera_stream_url: str | None = Field(
        default=None,
        max_length=500,
        description="An RTSP/HTTP source URL for GET /api/sensors/{id}/live "
        "(app/api/camera_live.py) to open and re-proxy as an MJPEG stream, so the dashboard shows "
        "a real live view without the browser ever connecting to the camera directly. Write-only "
        "in practice: GET /api/sensor-registrations and the PUT response below always report this "
        "as null regardless of what's actually stored (see app/api/sensor_registry.py) since a "
        "camera's stream URL commonly embeds its own login credentials -- there's no legitimate "
        "reason a dashboard viewer needs it back once it's set, only the proxy endpoint that "
        "already holds it server-side.",
    )


class SensorRegistration(SensorRegistrationInput):
    sensor_id: str
    site_id: int | None = None


class AuthorizedOperatorInput(BaseModel):
    """A known/authorized drone operator (e.g. an FAA Remote ID operator
    ID) whose aircraft should be classified FRIENDLY. See app/allowlist.py.
    public_key is a base64-encoded Ed25519 public key (see
    app/remote_id.py) -- a detection claiming this operator_id must carry
    a signature verifiable against it to be trusted; the ID string alone
    proves nothing.
    """

    name: str = Field(min_length=1, max_length=200)
    public_key: str | None = Field(
        default=None, description="Base64-encoded Ed25519 public key (see app/remote_id.py)"
    )
    active: bool = True


class AuthorizedOperator(AuthorizedOperatorInput):
    operator_id: str
    site_id: int | None = None


class AuditLogEntry(BaseModel):
    """One recorded admin action -- see app/schema.py's audit_log table
    for which actions get recorded and why.
    """

    id: int | None = None
    site_id: int | None = None
    occurred_at: datetime
    actor: str
    action: str
    target: str | None = None
    detail: str | None = None


class ApiKeyStatus(BaseModel):
    """One configured key's non-secret metadata plus its recorded usage,
    for GET /api/admin/keys -- never the raw key itself (see
    app/auth.py's _configured_keys(); the raw key only ever exists
    in-process, matched against the caller's X-API-Key header).
    """

    label: str
    role: str
    site: str | None = None
    revoked: bool = False
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    use_count: int = 0
