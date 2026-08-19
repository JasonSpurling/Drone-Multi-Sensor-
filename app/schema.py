"""SQLAlchemy Core table definitions -- the single source of truth for the
database schema, portable across SQLite (the zero-config default) and
PostgreSQL (set DRONE_DATABASE_URL=postgresql+psycopg2://... for production
deployments that need concurrent-write throughput SQLite can't offer).

Timestamps are stored as ISO-8601 strings (not native TIMESTAMP columns) to
keep the exact naive-UTC datetime round-trip behavior the app already
relies on identical across both backends.
"""

from __future__ import annotations

from sqlalchemy import Column, Float, ForeignKey, Index, Integer, MetaData, String, Table, Text

metadata = MetaData()

zone = Table(
    "zone",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(200), nullable=False),
    Column("zone_type", String(50), nullable=False),
    Column("polygon", Text, nullable=False),  # JSON list of [lat, lon] pairs
    Column("min_altitude_m", Float),
    Column("max_altitude_m", Float),
    Column("active", Integer, nullable=False, server_default="1"),  # 0/1
)

track = Table(
    "track",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("track_uid", String(64), nullable=False, unique=True),
    Column("first_seen", String(40), nullable=False),
    Column("last_seen", String(40), nullable=False),
    Column("status", String(20), nullable=False, server_default="active"),
    Column("classification", String(20), nullable=False, server_default="unknown"),
    Column("latitude", Float),
    Column("longitude", Float),
    Column("altitude_m", Float),
    Column("heading_deg", Float),
    Column("speed_mps", Float),
    Column("position_uncertainty_m", Float),
)

# IMM (Interacting Multiple Model) filter state for a track's motion
# estimate, kept separate from the public `track` row: ref_lat/ref_lon
# anchor the local tangent-plane frame the filter runs in (see
# app/geo.py), models is a JSON list of each mode's {x,y,vx,vy,covariance}
# (see app/imm.py -- CRUISE and MANEUVER, in that order), and
# mode_probabilities is a JSON [cruise_probability, maneuver_probability]
# pair -- all internal to the tracker, not exposed via the API.
track_kalman_state = Table(
    "track_kalman_state",
    metadata,
    Column("track_id", Integer, ForeignKey("track.id"), primary_key=True),
    Column("ref_lat", Float, nullable=False),
    Column("ref_lon", Float, nullable=False),
    Column("models", Text, nullable=False),
    Column("mode_probabilities", Text, nullable=False),
    Column("updated_at", String(40), nullable=False),
)

detection = Table(
    "detection",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("sensor_id", String(100), nullable=False),
    Column("sensor_type", String(20), nullable=False),
    Column("timestamp", String(40), nullable=False),
    Column("track_id", Integer, ForeignKey("track.id")),
    Column("latitude", Float),
    Column("longitude", Float),
    Column("altitude_m", Float),
    Column("azimuth_deg", Float),
    Column("range_m", Float),
    Column("confidence", Float, nullable=False, server_default="1.0"),
    Column("raw_data", Text),  # JSON blob
    Index("idx_detection_track_id", "track_id"),
    Index("idx_detection_timestamp", "timestamp"),
)

incident = Table(
    "incident",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("incident_uid", String(64), nullable=False, unique=True),
    Column("incident_type", String(50), nullable=False),
    Column("severity", String(20), nullable=False, server_default="low"),
    Column("status", String(20), nullable=False, server_default="open"),
    Column("track_id", Integer, ForeignKey("track.id")),
    Column("zone_id", Integer, ForeignKey("zone.id")),
    Column("opened_at", String(40), nullable=False),
    Column("closed_at", String(40)),
    Column("description", Text),
    Column("acknowledged_by", String(100)),
    Index("idx_incident_track_id", "track_id"),
    Index("idx_incident_zone_id", "zone_id"),
)

# A registered sensor's fixed mounting position/orientation, used to
# georeference detections that report azimuth/range instead of lat/lon
# (see app/georeference.py).
sensor_registry = Table(
    "sensor_registry",
    metadata,
    Column("sensor_id", String(100), primary_key=True),
    Column("sensor_type", String(20), nullable=False),
    Column("latitude", Float, nullable=False),
    Column("longitude", Float, nullable=False),
    Column("altitude_m", Float),
    Column("azimuth_reference_deg", Float, nullable=False, server_default="0.0"),
    Column("active", Integer, nullable=False, server_default="1"),
)

# Known/authorized drone operators (e.g. FAA Remote ID operator IDs) whose
# aircraft should be classified FRIENDLY rather than flagged as a threat.
# See app/allowlist.py.
authorized_operator = Table(
    "authorized_operator",
    metadata,
    Column("operator_id", String(100), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("active", Integer, nullable=False, server_default="1"),
)
