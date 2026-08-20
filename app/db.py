"""Database engine and storage helpers, portable across SQLite (default)
and PostgreSQL (DRONE_DATABASE_URL). Queries are plain SQL via SQLAlchemy
Core's text(), not the ORM -- the schema is small and the raw-SQL shape
made porting from the original sqlite3-only version mechanical and easy to
verify against both backends.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Connection, Engine

from app.config import DATABASE_URL, DB_MAX_OVERFLOW, DB_POOL_SIZE
from app.models import Detection, Incident, Track, Zone
from app.schema import metadata

# pool_size/max_overflow are QueuePool-specific -- SQLite doesn't use
# QueuePool (SQLAlchemy defaults it to NullPool/SingletonThreadPool
# depending on the URL), and passing those kwargs to an engine using a pool
# class that doesn't accept them raises a TypeError. Only apply them for a
# real (PostgreSQL) connection pool.
_engine_kwargs: dict = {"future": True}
if not DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["pool_size"] = DB_POOL_SIZE
    _engine_kwargs["max_overflow"] = DB_MAX_OVERFLOW

engine: Engine = create_engine(DATABASE_URL, **_engine_kwargs)


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    if engine.dialect.name == "sqlite":
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()


@contextmanager
def db_session() -> Iterator[Connection]:
    with engine.begin() as conn:
        yield conn


# Columns added to existing tables after their initial release.
# metadata.create_all only creates a table on a fresh database, so an
# existing database needs these added explicitly to pick them up.
_TABLE_MIGRATION_COLUMNS = {
    "track": {
        "heading_deg": "REAL",
        "speed_mps": "REAL",
        "position_uncertainty_m": "REAL",
        "maneuver_probability": "REAL",
    },
    "authorized_operator": {
        "public_key": "VARCHAR(64)",
    },
    "detection": {
        "georeferenced": "INTEGER DEFAULT 0",
    },
}


def _migrate_table_columns() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _TABLE_MIGRATION_COLUMNS.items():
            if table not in existing_tables:
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table)}
            for column, sql_type in columns.items():
                if column not in existing_columns:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))


def _migrate_kalman_state_table() -> None:
    """track_kalman_state's shape changed when tracking.py moved from a
    single constant-velocity filter to an IMM (app/imm.py): the old
    x_m/y_m/vx_mps/vy_mps/covariance columns became a `models` JSON list
    plus `mode_probabilities`. This table holds only ephemeral, derivable
    filter state (not audit data), so on detecting the old shape we just
    drop and let create_all recreate it -- any in-flight tracks simply
    reinitialize their filter on their next detection.
    """
    inspector = inspect(engine)
    if "track_kalman_state" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("track_kalman_state")}
    if "x_m" in existing and "models" not in existing:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE track_kalman_state"))


def _migrate_indexes() -> None:
    """metadata.create_all only creates indexes as part of creating a new
    table -- it doesn't add an index to a table that already exists (unlike
    a fresh database, which gets it for free from the track table's
    definition in schema.py). CREATE INDEX IF NOT EXISTS is supported by
    both SQLite and PostgreSQL, so this is safe to run on every startup.
    """
    with engine.begin() as conn:
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS idx_track_status_last_seen ON track (status, last_seen)")
        )


def init_db() -> None:
    _migrate_kalman_state_table()
    metadata.create_all(engine, checkfirst=True)
    _migrate_table_columns()
    _migrate_indexes()


# --- Detection helpers -----------------------------------------------------

def create_detection(detection: Detection) -> Detection:
    with db_session() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO detection
                    (sensor_id, sensor_type, timestamp, track_id, latitude, longitude,
                     altitude_m, azimuth_deg, range_m, confidence, raw_data, georeferenced)
                VALUES (:sensor_id, :sensor_type, :timestamp, :track_id, :latitude, :longitude,
                        :altitude_m, :azimuth_deg, :range_m, :confidence, :raw_data, :georeferenced)
                RETURNING id
                """
            ),
            {
                "sensor_id": detection.sensor_id,
                "sensor_type": detection.sensor_type.value,
                "timestamp": detection.timestamp.isoformat(),
                "track_id": detection.track_id,
                "latitude": detection.latitude,
                "longitude": detection.longitude,
                "altitude_m": detection.altitude_m,
                "azimuth_deg": detection.azimuth_deg,
                "range_m": detection.range_m,
                "confidence": detection.confidence,
                "raw_data": json.dumps(detection.raw_data) if detection.raw_data is not None else None,
                "georeferenced": int(detection.georeferenced),
            },
        ).one()
        detection.id = row.id
    return detection


def get_detection(detection_id: int) -> Detection | None:
    with db_session() as conn:
        row = conn.execute(
            text("SELECT * FROM detection WHERE id = :id"), {"id": detection_id}
        ).mappings().fetchone()
    return _row_to_detection(row) if row else None


def list_detections(
    track_id: int | None = None, limit: int | None = None, offset: int = 0
) -> list[Detection]:
    with db_session() as conn:
        if track_id is not None:
            query = "SELECT * FROM detection WHERE track_id = :track_id ORDER BY timestamp"
            params: dict = {"track_id": track_id}
        else:
            query = "SELECT * FROM detection ORDER BY timestamp"
            params = {}
        if limit is not None:
            query += " LIMIT :limit OFFSET :offset"
            params.update(limit=limit, offset=offset)
        rows = conn.execute(text(query), params).mappings().all()
    return [_row_to_detection(row) for row in rows]


def list_recent_detections(track_id: int, limit: int) -> list[Detection]:
    """Most recent `limit` detections for a track, for classification
    fusion (app/fusion.py) -- order doesn't matter to a weighted vote, only
    which detections are included.
    """
    with db_session() as conn:
        rows = conn.execute(
            text(
                "SELECT * FROM detection WHERE track_id = :track_id "
                "ORDER BY timestamp DESC LIMIT :limit"
            ),
            {"track_id": track_id, "limit": limit},
        ).mappings().all()
    return [_row_to_detection(row) for row in rows]


def purge_old_detections(before: datetime) -> int:
    """Delete detections older than `before`. Returns the number removed."""
    with db_session() as conn:
        result = conn.execute(
            text("DELETE FROM detection WHERE timestamp < :before"), {"before": before.isoformat()}
        )
    return result.rowcount


def _row_to_detection(row) -> Detection:
    return Detection(
        id=row["id"],
        sensor_id=row["sensor_id"],
        sensor_type=row["sensor_type"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        track_id=row["track_id"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        altitude_m=row["altitude_m"],
        azimuth_deg=row["azimuth_deg"],
        range_m=row["range_m"],
        confidence=row["confidence"],
        raw_data=json.loads(row["raw_data"]) if row["raw_data"] else None,
        georeferenced=bool(row["georeferenced"]),
    )


# --- Track helpers -----------------------------------------------------

def create_track(track: Track) -> Track:
    with db_session() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO track
                    (track_uid, first_seen, last_seen, status, classification,
                     latitude, longitude, altitude_m, heading_deg, speed_mps,
                     position_uncertainty_m, maneuver_probability)
                VALUES (:track_uid, :first_seen, :last_seen, :status, :classification,
                        :latitude, :longitude, :altitude_m, :heading_deg, :speed_mps,
                        :position_uncertainty_m, :maneuver_probability)
                RETURNING id
                """
            ),
            {
                "track_uid": track.track_uid,
                "first_seen": track.first_seen.isoformat(),
                "last_seen": track.last_seen.isoformat(),
                "status": track.status.value,
                "classification": track.classification.value,
                "latitude": track.latitude,
                "longitude": track.longitude,
                "altitude_m": track.altitude_m,
                "heading_deg": track.heading_deg,
                "speed_mps": track.speed_mps,
                "position_uncertainty_m": track.position_uncertainty_m,
                "maneuver_probability": track.maneuver_probability,
            },
        ).one()
        track.id = row.id
    return track


def update_track(track: Track) -> Track:
    with db_session() as conn:
        conn.execute(
            text(
                """
                UPDATE track
                SET last_seen = :last_seen, status = :status, classification = :classification,
                    latitude = :latitude, longitude = :longitude, altitude_m = :altitude_m,
                    heading_deg = :heading_deg, speed_mps = :speed_mps,
                    position_uncertainty_m = :position_uncertainty_m,
                    maneuver_probability = :maneuver_probability
                WHERE id = :id
                """
            ),
            {
                "last_seen": track.last_seen.isoformat(),
                "status": track.status.value,
                "classification": track.classification.value,
                "latitude": track.latitude,
                "longitude": track.longitude,
                "altitude_m": track.altitude_m,
                "heading_deg": track.heading_deg,
                "speed_mps": track.speed_mps,
                "position_uncertainty_m": track.position_uncertainty_m,
                "maneuver_probability": track.maneuver_probability,
                "id": track.id,
            },
        )
    return track


def get_track(track_id: int) -> Track | None:
    with db_session() as conn:
        row = conn.execute(
            text("SELECT * FROM track WHERE id = :id"), {"id": track_id}
        ).mappings().fetchone()
    return _row_to_track(row) if row else None


def list_tracks(
    status: str | None = None, limit: int | None = None, offset: int = 0
) -> list[Track]:
    with db_session() as conn:
        if status is not None:
            query = "SELECT * FROM track WHERE status = :status ORDER BY last_seen DESC"
            params: dict = {"status": status}
        else:
            query = "SELECT * FROM track ORDER BY last_seen DESC"
            params = {}
        if limit is not None:
            query += " LIMIT :limit OFFSET :offset"
            params.update(limit=limit, offset=offset)
        rows = conn.execute(text(query), params).mappings().all()
    return [_row_to_track(row) for row in rows]


def _row_to_track(row) -> Track:
    return Track(
        id=row["id"],
        track_uid=row["track_uid"],
        first_seen=datetime.fromisoformat(row["first_seen"]),
        last_seen=datetime.fromisoformat(row["last_seen"]),
        status=row["status"],
        classification=row["classification"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        altitude_m=row["altitude_m"],
        heading_deg=row["heading_deg"],
        speed_mps=row["speed_mps"],
        position_uncertainty_m=row["position_uncertainty_m"],
        maneuver_probability=row["maneuver_probability"],
    )


# --- IMM filter state helpers -----------------------------------------

class KalmanStateRecord:
    """Persisted IMM filter state for one track: the local tangent-plane
    reference point, each mode's state vector + covariance, and the mode
    probabilities. See app/imm.py.
    """

    def __init__(
        self,
        track_id: int,
        ref_lat: float,
        ref_lon: float,
        models: list[dict],
        mode_probabilities: list[float],
        updated_at: datetime,
    ) -> None:
        self.track_id = track_id
        self.ref_lat = ref_lat
        self.ref_lon = ref_lon
        self.models = models
        self.mode_probabilities = mode_probabilities
        self.updated_at = updated_at


def upsert_kalman_state(record: KalmanStateRecord) -> None:
    with db_session() as conn:
        conn.execute(
            text(
                """
                INSERT INTO track_kalman_state
                    (track_id, ref_lat, ref_lon, models, mode_probabilities, updated_at)
                VALUES (:track_id, :ref_lat, :ref_lon, :models, :mode_probabilities, :updated_at)
                ON CONFLICT (track_id) DO UPDATE SET
                    models = excluded.models, mode_probabilities = excluded.mode_probabilities,
                    updated_at = excluded.updated_at
                """
            ),
            {
                "track_id": record.track_id,
                "ref_lat": record.ref_lat,
                "ref_lon": record.ref_lon,
                "models": json.dumps(record.models),
                "mode_probabilities": json.dumps(record.mode_probabilities),
                "updated_at": record.updated_at.isoformat(),
            },
        )


def get_kalman_state(track_id: int) -> KalmanStateRecord | None:
    with db_session() as conn:
        row = conn.execute(
            text("SELECT * FROM track_kalman_state WHERE track_id = :track_id"),
            {"track_id": track_id},
        ).mappings().fetchone()
    if row is None:
        return None
    return KalmanStateRecord(
        track_id=row["track_id"],
        ref_lat=row["ref_lat"],
        ref_lon=row["ref_lon"],
        models=json.loads(row["models"]),
        mode_probabilities=json.loads(row["mode_probabilities"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


# --- Incident helpers -----------------------------------------------------

def create_incident(incident: Incident) -> Incident:
    with db_session() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO incident
                    (incident_uid, incident_type, severity, status, track_id, zone_id,
                     opened_at, closed_at, description, acknowledged_by)
                VALUES (:incident_uid, :incident_type, :severity, :status, :track_id, :zone_id,
                        :opened_at, :closed_at, :description, :acknowledged_by)
                RETURNING id
                """
            ),
            {
                "incident_uid": incident.incident_uid,
                "incident_type": incident.incident_type.value,
                "severity": incident.severity.value,
                "status": incident.status.value,
                "track_id": incident.track_id,
                "zone_id": incident.zone_id,
                "opened_at": incident.opened_at.isoformat(),
                "closed_at": incident.closed_at.isoformat() if incident.closed_at else None,
                "description": incident.description,
                "acknowledged_by": incident.acknowledged_by,
            },
        ).one()
        incident.id = row.id
    return incident


def get_incident(incident_id: int) -> Incident | None:
    with db_session() as conn:
        row = conn.execute(
            text("SELECT * FROM incident WHERE id = :id"), {"id": incident_id}
        ).mappings().fetchone()
    return _row_to_incident(row) if row else None


def update_incident(incident: Incident) -> Incident:
    with db_session() as conn:
        conn.execute(
            text(
                """
                UPDATE incident
                SET status = :status, closed_at = :closed_at, description = :description,
                    acknowledged_by = :acknowledged_by
                WHERE id = :id
                """
            ),
            {
                "status": incident.status.value,
                "closed_at": incident.closed_at.isoformat() if incident.closed_at else None,
                "description": incident.description,
                "acknowledged_by": incident.acknowledged_by,
                "id": incident.id,
            },
        )
    return incident


def get_open_incident(track_id: int, zone_id: int, incident_type: str) -> Incident | None:
    with db_session() as conn:
        row = conn.execute(
            text(
                """
                SELECT * FROM incident
                WHERE track_id = :track_id AND zone_id = :zone_id AND incident_type = :incident_type
                    AND status != 'resolved'
                ORDER BY opened_at DESC LIMIT 1
                """
            ),
            {"track_id": track_id, "zone_id": zone_id, "incident_type": incident_type},
        ).mappings().fetchone()
    return _row_to_incident(row) if row else None


def get_open_behavioral_incident(track_id: int, incident_type: str) -> Incident | None:
    """Like get_open_incident, but for a behavioral incident (loitering,
    formation, shadowing -- app/behavior.py) that isn't tied to any zone.
    `zone_id = :zone_id` in the zone-based query would never match a NULL
    zone_id (SQL NULL comparison), so this uses IS NULL instead of
    reusing that query with zone_id=None.
    """
    with db_session() as conn:
        row = conn.execute(
            text(
                """
                SELECT * FROM incident
                WHERE track_id = :track_id AND zone_id IS NULL AND incident_type = :incident_type
                    AND status != 'resolved'
                ORDER BY opened_at DESC LIMIT 1
                """
            ),
            {"track_id": track_id, "incident_type": incident_type},
        ).mappings().fetchone()
    return _row_to_incident(row) if row else None


def list_incidents(
    status: str | None = None, limit: int | None = None, offset: int = 0
) -> list[Incident]:
    with db_session() as conn:
        if status is not None:
            query = "SELECT * FROM incident WHERE status = :status ORDER BY opened_at DESC"
            params: dict = {"status": status}
        else:
            query = "SELECT * FROM incident ORDER BY opened_at DESC"
            params = {}
        if limit is not None:
            query += " LIMIT :limit OFFSET :offset"
            params.update(limit=limit, offset=offset)
        rows = conn.execute(text(query), params).mappings().all()
    return [_row_to_incident(row) for row in rows]


def list_incidents_in_range(start: datetime, end: datetime) -> list[Incident]:
    """All incidents opened in [start, end) -- for app/reporting.py's
    compliance/analytics rollups, which need a bounded window rather than
    list_incidents' "most recent N" pagination.
    """
    with db_session() as conn:
        rows = conn.execute(
            text("SELECT * FROM incident WHERE opened_at >= :start AND opened_at < :end ORDER BY opened_at"),
            {"start": start.isoformat(), "end": end.isoformat()},
        ).mappings().all()
    return [_row_to_incident(row) for row in rows]


def _row_to_incident(row) -> Incident:
    return Incident(
        id=row["id"],
        incident_uid=row["incident_uid"],
        incident_type=row["incident_type"],
        severity=row["severity"],
        status=row["status"],
        track_id=row["track_id"],
        zone_id=row["zone_id"],
        opened_at=datetime.fromisoformat(row["opened_at"]),
        closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
        description=row["description"],
        acknowledged_by=row["acknowledged_by"],
    )


# --- Zone helpers -----------------------------------------------------

def create_zone(zone: Zone) -> Zone:
    with db_session() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO zone (name, zone_type, polygon, min_altitude_m, max_altitude_m, active)
                VALUES (:name, :zone_type, :polygon, :min_altitude_m, :max_altitude_m, :active)
                RETURNING id
                """
            ),
            {
                "name": zone.name,
                "zone_type": zone.zone_type.value,
                "polygon": json.dumps(zone.polygon),
                "min_altitude_m": zone.min_altitude_m,
                "max_altitude_m": zone.max_altitude_m,
                "active": int(zone.active),
            },
        ).one()
        zone.id = row.id
    return zone


def get_zone(zone_id: int) -> Zone | None:
    with db_session() as conn:
        row = conn.execute(
            text("SELECT * FROM zone WHERE id = :id"), {"id": zone_id}
        ).mappings().fetchone()
    return _row_to_zone(row) if row else None


def get_zone_by_name(name: str) -> Zone | None:
    with db_session() as conn:
        row = conn.execute(
            text("SELECT * FROM zone WHERE name = :name"), {"name": name}
        ).mappings().fetchone()
    return _row_to_zone(row) if row else None


def list_zones(active_only: bool = False) -> list[Zone]:
    with db_session() as conn:
        if active_only:
            rows = conn.execute(
                text("SELECT * FROM zone WHERE active = 1 ORDER BY name")
            ).mappings().all()
        else:
            rows = conn.execute(text("SELECT * FROM zone ORDER BY name")).mappings().all()
    return [_row_to_zone(row) for row in rows]


def _row_to_zone(row) -> Zone:
    return Zone(
        id=row["id"],
        name=row["name"],
        zone_type=row["zone_type"],
        polygon=json.loads(row["polygon"]),
        min_altitude_m=row["min_altitude_m"],
        max_altitude_m=row["max_altitude_m"],
        active=bool(row["active"]),
    )


# --- Sensor registry helpers -------------------------------------------

def upsert_sensor_registration(
    sensor_id: str,
    sensor_type: str,
    latitude: float,
    longitude: float,
    altitude_m: float | None,
    azimuth_reference_deg: float,
    active: bool = True,
) -> None:
    with db_session() as conn:
        conn.execute(
            text(
                """
                INSERT INTO sensor_registry
                    (sensor_id, sensor_type, latitude, longitude, altitude_m,
                     azimuth_reference_deg, active)
                VALUES (:sensor_id, :sensor_type, :latitude, :longitude, :altitude_m,
                        :azimuth_reference_deg, :active)
                ON CONFLICT (sensor_id) DO UPDATE SET
                    sensor_type = excluded.sensor_type, latitude = excluded.latitude,
                    longitude = excluded.longitude, altitude_m = excluded.altitude_m,
                    azimuth_reference_deg = excluded.azimuth_reference_deg,
                    active = excluded.active
                """
            ),
            {
                "sensor_id": sensor_id,
                "sensor_type": sensor_type,
                "latitude": latitude,
                "longitude": longitude,
                "altitude_m": altitude_m,
                "azimuth_reference_deg": azimuth_reference_deg,
                "active": int(active),
            },
        )


def get_sensor_registration(sensor_id: str) -> dict | None:
    with db_session() as conn:
        row = conn.execute(
            text("SELECT * FROM sensor_registry WHERE sensor_id = :sensor_id AND active = 1"),
            {"sensor_id": sensor_id},
        ).mappings().fetchone()
    return dict(row) if row else None


def list_sensor_registrations() -> list[dict]:
    with db_session() as conn:
        rows = conn.execute(
            text("SELECT * FROM sensor_registry ORDER BY sensor_id")
        ).mappings().all()
    return [dict(row) for row in rows]


# --- Authorized operator (friendly allowlist) helpers -----------------

def upsert_authorized_operator(
    operator_id: str, name: str, public_key: str | None = None, active: bool = True
) -> None:
    with db_session() as conn:
        conn.execute(
            text(
                """
                INSERT INTO authorized_operator (operator_id, name, public_key, active)
                VALUES (:operator_id, :name, :public_key, :active)
                ON CONFLICT (operator_id) DO UPDATE SET
                    name = excluded.name, public_key = excluded.public_key, active = excluded.active
                """
            ),
            {"operator_id": operator_id, "name": name, "public_key": public_key, "active": int(active)},
        )


def get_authorized_operator(operator_id: str) -> dict | None:
    with db_session() as conn:
        row = conn.execute(
            text("SELECT * FROM authorized_operator WHERE operator_id = :operator_id AND active = 1"),
            {"operator_id": operator_id},
        ).mappings().fetchone()
    return dict(row) if row else None


def list_authorized_operators() -> list[dict]:
    with db_session() as conn:
        rows = conn.execute(
            text("SELECT * FROM authorized_operator ORDER BY operator_id")
        ).mappings().all()
    return [dict(row) for row in rows]
