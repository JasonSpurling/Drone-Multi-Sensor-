"""SQLite connection management and storage helpers."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from app.models import Detection, Incident, Track, Zone

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "drone_sensor.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_session() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db_session() as conn:
        conn.executescript(SCHEMA_PATH.read_text())


# --- Detection helpers -----------------------------------------------------

def create_detection(detection: Detection) -> Detection:
    with db_session() as conn:
        cur = conn.execute(
            """
            INSERT INTO detection
                (sensor_id, sensor_type, timestamp, track_id, latitude, longitude,
                 altitude_m, azimuth_deg, range_m, confidence, raw_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                detection.sensor_id,
                detection.sensor_type.value,
                detection.timestamp.isoformat(),
                detection.track_id,
                detection.latitude,
                detection.longitude,
                detection.altitude_m,
                detection.azimuth_deg,
                detection.range_m,
                detection.confidence,
                json.dumps(detection.raw_data) if detection.raw_data is not None else None,
            ),
        )
        detection.id = cur.lastrowid
    return detection


def get_detection(detection_id: int) -> Detection | None:
    with db_session() as conn:
        row = conn.execute("SELECT * FROM detection WHERE id = ?", (detection_id,)).fetchone()
    return _row_to_detection(row) if row else None


def list_detections(track_id: int | None = None) -> list[Detection]:
    with db_session() as conn:
        if track_id is not None:
            rows = conn.execute(
                "SELECT * FROM detection WHERE track_id = ? ORDER BY timestamp", (track_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM detection ORDER BY timestamp").fetchall()
    return [_row_to_detection(row) for row in rows]


def _row_to_detection(row: sqlite3.Row) -> Detection:
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
    )


# --- Track helpers -----------------------------------------------------

def create_track(track: Track) -> Track:
    with db_session() as conn:
        cur = conn.execute(
            """
            INSERT INTO track
                (track_uid, first_seen, last_seen, status, classification,
                 latitude, longitude, altitude_m)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                track.track_uid,
                track.first_seen.isoformat(),
                track.last_seen.isoformat(),
                track.status.value,
                track.classification.value,
                track.latitude,
                track.longitude,
                track.altitude_m,
            ),
        )
        track.id = cur.lastrowid
    return track


def update_track(track: Track) -> Track:
    with db_session() as conn:
        conn.execute(
            """
            UPDATE track
            SET last_seen = ?, status = ?, classification = ?,
                latitude = ?, longitude = ?, altitude_m = ?
            WHERE id = ?
            """,
            (
                track.last_seen.isoformat(),
                track.status.value,
                track.classification.value,
                track.latitude,
                track.longitude,
                track.altitude_m,
                track.id,
            ),
        )
    return track


def get_track(track_id: int) -> Track | None:
    with db_session() as conn:
        row = conn.execute("SELECT * FROM track WHERE id = ?", (track_id,)).fetchone()
    return _row_to_track(row) if row else None


def list_tracks(status: str | None = None) -> list[Track]:
    with db_session() as conn:
        if status is not None:
            rows = conn.execute(
                "SELECT * FROM track WHERE status = ? ORDER BY last_seen DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM track ORDER BY last_seen DESC").fetchall()
    return [_row_to_track(row) for row in rows]


def _row_to_track(row: sqlite3.Row) -> Track:
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
    )


# --- Incident helpers -----------------------------------------------------

def create_incident(incident: Incident) -> Incident:
    with db_session() as conn:
        cur = conn.execute(
            """
            INSERT INTO incident
                (incident_uid, incident_type, severity, status, track_id, zone_id,
                 opened_at, closed_at, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident.incident_uid,
                incident.incident_type.value,
                incident.severity.value,
                incident.status.value,
                incident.track_id,
                incident.zone_id,
                incident.opened_at.isoformat(),
                incident.closed_at.isoformat() if incident.closed_at else None,
                incident.description,
            ),
        )
        incident.id = cur.lastrowid
    return incident


def get_incident(incident_id: int) -> Incident | None:
    with db_session() as conn:
        row = conn.execute("SELECT * FROM incident WHERE id = ?", (incident_id,)).fetchone()
    return _row_to_incident(row) if row else None


def update_incident(incident: Incident) -> Incident:
    with db_session() as conn:
        conn.execute(
            """
            UPDATE incident
            SET status = ?, closed_at = ?, description = ?
            WHERE id = ?
            """,
            (
                incident.status.value,
                incident.closed_at.isoformat() if incident.closed_at else None,
                incident.description,
                incident.id,
            ),
        )
    return incident


def get_open_incident(track_id: int, zone_id: int, incident_type: str) -> Incident | None:
    with db_session() as conn:
        row = conn.execute(
            """
            SELECT * FROM incident
            WHERE track_id = ? AND zone_id = ? AND incident_type = ? AND status != 'resolved'
            ORDER BY opened_at DESC LIMIT 1
            """,
            (track_id, zone_id, incident_type),
        ).fetchone()
    return _row_to_incident(row) if row else None


def list_incidents(status: str | None = None) -> list[Incident]:
    with db_session() as conn:
        if status is not None:
            rows = conn.execute(
                "SELECT * FROM incident WHERE status = ? ORDER BY opened_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM incident ORDER BY opened_at DESC").fetchall()
    return [_row_to_incident(row) for row in rows]


def _row_to_incident(row: sqlite3.Row) -> Incident:
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
    )


# --- Zone helpers -----------------------------------------------------

def create_zone(zone: Zone) -> Zone:
    with db_session() as conn:
        cur = conn.execute(
            """
            INSERT INTO zone (name, zone_type, polygon, min_altitude_m, max_altitude_m, active)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                zone.name,
                zone.zone_type.value,
                json.dumps(zone.polygon),
                zone.min_altitude_m,
                zone.max_altitude_m,
                int(zone.active),
            ),
        )
        zone.id = cur.lastrowid
    return zone


def get_zone(zone_id: int) -> Zone | None:
    with db_session() as conn:
        row = conn.execute("SELECT * FROM zone WHERE id = ?", (zone_id,)).fetchone()
    return _row_to_zone(row) if row else None


def get_zone_by_name(name: str) -> Zone | None:
    with db_session() as conn:
        row = conn.execute("SELECT * FROM zone WHERE name = ?", (name,)).fetchone()
    return _row_to_zone(row) if row else None


def list_zones(active_only: bool = False) -> list[Zone]:
    with db_session() as conn:
        if active_only:
            rows = conn.execute("SELECT * FROM zone WHERE active = 1 ORDER BY name").fetchall()
        else:
            rows = conn.execute("SELECT * FROM zone ORDER BY name").fetchall()
    return [_row_to_zone(row) for row in rows]


def _row_to_zone(row: sqlite3.Row) -> Zone:
    return Zone(
        id=row["id"],
        name=row["name"],
        zone_type=row["zone_type"],
        polygon=json.loads(row["polygon"]),
        min_altitude_m=row["min_altitude_m"],
        max_altitude_m=row["max_altitude_m"],
        active=bool(row["active"]),
    )
