"""Exports a track's detection history (app/api/tracks.py's
GET /api/tracks/{id}/history) into formats a human or another tool can
actually open, for post-incident review: GPX for GIS/mapping software,
KML for Google Earth, CSV for a spreadsheet. Pure string-building
functions -- no I/O, no framework dependency -- so they're testable
without a running app.

Detections missing latitude/longitude (an azimuth/range detection that
somehow never got georeferenced, or a raw radar detection reported before
a sensor was registered) are skipped in the geo formats (GPX/KML both
require a coordinate per point) but kept in the CSV, which has no such
constraint and is meant to be a complete record.
"""

from __future__ import annotations

import csv
import io
from xml.sax.saxutils import escape

from app.models import Detection, Incident, Track


def _geo_points(detections: list[Detection]) -> list[Detection]:
    return [d for d in detections if d.latitude is not None and d.longitude is not None]


def to_gpx(track: Track, detections: list[Detection]) -> str:
    points = _geo_points(detections)
    trkpts = "\n".join(
        f'      <trkpt lat="{d.latitude}" lon="{d.longitude}">\n'
        f"        <time>{d.timestamp.isoformat()}Z</time>\n"
        + (f"        <ele>{d.altitude_m}</ele>\n" if d.altitude_m is not None else "")
        + "      </trkpt>"
        for d in points
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="Drone Multi-Sensor Tracker" '
        'xmlns="http://www.topografix.com/GPX/1/1">\n'
        "  <trk>\n"
        f"    <name>{escape(track.track_uid)}</name>\n"
        f"    <desc>classification={escape(track.classification.value)}</desc>\n"
        "    <trkseg>\n"
        f"{trkpts}\n"
        "    </trkseg>\n"
        "  </trk>\n"
        "</gpx>\n"
    )


def to_kml(track: Track, detections: list[Detection]) -> str:
    points = _geo_points(detections)
    # KML coordinates are lon,lat[,alt] -- the opposite axis order to GPX's
    # lat/lon attributes, and comma- not space-separated.
    coordinates = " ".join(
        f"{d.longitude},{d.latitude},{d.altitude_m if d.altitude_m is not None else 0}" for d in points
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2">\n'
        "  <Document>\n"
        f"    <name>Track {escape(track.track_uid)}</name>\n"
        "    <Placemark>\n"
        f"      <name>{escape(track.track_uid)}</name>\n"
        f"      <description>classification={escape(track.classification.value)}</description>\n"
        "      <LineString>\n"
        "        <altitudeMode>absolute</altitudeMode>\n"
        f"        <coordinates>{coordinates}</coordinates>\n"
        "      </LineString>\n"
        "    </Placemark>\n"
        "  </Document>\n"
        "</kml>\n"
    )


def incidents_to_csv(incidents: list[Incident]) -> str:
    """A per-incident audit record (not the aggregate rollup in
    app/reporting.py) -- for a compliance officer who needs the raw list
    behind a summary count, e.g. to answer "which specific incidents".
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "incident_uid", "incident_type", "severity", "status", "track_id", "zone_id",
        "opened_at", "closed_at", "acknowledged_by", "description",
    ])
    for i in incidents:
        writer.writerow([
            i.incident_uid, i.incident_type.value, i.severity.value, i.status.value,
            i.track_id, i.zone_id, i.opened_at.isoformat(),
            i.closed_at.isoformat() if i.closed_at else "", i.acknowledged_by or "", i.description or "",
        ])
    return buffer.getvalue()


def labeled_detections_to_training_csv(detections: list[Detection]) -> str:
    """Every human_label-tagged detection (see app.db.list_labeled_detections),
    in exactly the CSV shape app/ml/train.py's load_csv expects -- point
    `python -m app.ml.train --csv` straight at this endpoint's output
    (GET /api/ml/training-data/export) to train from real labeled traffic
    once there's enough of it. RF fields are pulled out of raw_data the
    same way app.ml.features.extract_features does for a live Detection,
    keeping this export and that inference-time extraction from drifting
    into two different ideas of what "the RF fields" are.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "sensor_type", "confidence", "altitude_m",
            "rf_center_frequency_mhz", "rf_bandwidth_mhz", "rf_frequency_hopping", "label",
        ]
    )
    for d in detections:
        raw = d.raw_data or {}
        writer.writerow([
            d.sensor_type.value,
            d.confidence,
            d.altitude_m if d.altitude_m is not None else "",
            raw.get("center_frequency_mhz", ""),
            raw.get("bandwidth_mhz", ""),
            "true" if raw.get("frequency_hopping") else "",
            d.human_label.value if d.human_label is not None else "",
        ])
    return buffer.getvalue()


def to_csv(detections: list[Detection]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["timestamp", "sensor_id", "sensor_type", "latitude", "longitude", "altitude_m", "confidence"]
    )
    for d in detections:
        writer.writerow([
            d.timestamp.isoformat(), d.sensor_id, d.sensor_type.value,
            d.latitude, d.longitude, d.altitude_m, d.confidence,
        ])
    return buffer.getvalue()
