"""Decodes EUROCONTROL ASTERIX CAT048 (Monoradar Target Reports) -- the
actual binary surveillance protocol most commercial primary/secondary
radars speak on their data network interface -- into this tracker's
detection payload format.

Uses the `asterix4py` library, which decodes against EUROCONTROL's
published XML category definitions (FSPEC-driven item presence, correct
Fixed/Repetitive/Variable/Compound field layouts per the actual CAT048
v1.14 spec) rather than a hand-rolled byte parser reimplementing that from
memory -- the same reasoning as using `ultralytics` for YOLO instead of
writing object detection from scratch. Only CAT048 is wired up here (the
common case for monoradar surveillance); `asterix4py` ships definitions for
several other categories (CAT021 ADS-B, CAT062 system tracks, ...) that
this module doesn't use but a deployment needing them could.

Only fields relevant to this tracker are pulled out of a decoded record:
measured polar position (item 040, required), flight level (item 090,
optional altitude), time of day (item 140), and a few identity fields
(SAC/SIC, aircraft address, callsign, Mode-3/A) kept in raw_data for
context. Everything else CAT048 carries (track velocity, quality
indicators, ACAS/BDS data, ...) is decoded by the library but not consumed
here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

_NM_TO_M = 1852.0
_FEET_TO_M = 0.3048


def _time_of_day_to_timestamp(time_of_day_s: float | None) -> datetime:
    """CAT048 item 140 is seconds since midnight UTC of the *radar's* day,
    not a full date. Combined with the current UTC date -- correct for a
    feed processed close to real-time; a message queued across a midnight
    boundary before reaching this bridge would land on the wrong day, a
    known limitation of ToD-only timestamps rather than something this
    module can resolve without a full ASTERIX time-of-day record.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    if time_of_day_s is None:
        return now
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(seconds=time_of_day_s)


def build_detection_payload(record: dict, sensor_id: str, confidence: float = 0.9) -> dict | None:
    """`record` is one decoded CAT048 record from
    `asterix4py.AsterixParser(datagram).get_result()` -- a dict like
    `{'cat': 48, '040': {'RHO': ..., 'THETA': ...}, '090': {...}, ...}`.
    Returns a POST /api/detections payload, or None if the record lacks
    the minimum field (measured polar position) to report at all.

    Reports azimuth_deg/range_m, not lat/lon -- register the radar's
    mounting position (see the README's Georeferencing section) or
    every detection from this adapter is silently dropped.
    """
    position = record.get("040")
    if not position or "RHO" not in position or "THETA" not in position:
        return None

    range_m = position["RHO"] * _NM_TO_M
    azimuth_deg = position["THETA"] % 360.0

    altitude_m = None
    flight_level = record.get("090")
    if flight_level and not flight_level.get("V") and not flight_level.get("G"):
        altitude_m = flight_level["FL"] * 100 * _FEET_TO_M

    timestamp = _time_of_day_to_timestamp((record.get("140") or {}).get("ToD"))

    raw_data: dict = {"protocol": "asterix_cat048"}
    source = record.get("010")
    if source:
        raw_data["sac"] = source.get("SAC")
        raw_data["sic"] = source.get("SIC")
    aircraft_address = (record.get("220") or {}).get("ACAddr")
    if aircraft_address:
        raw_data["aircraft_address"] = aircraft_address
    callsign = (record.get("240") or {}).get("TId")
    if callsign:
        raw_data["callsign"] = callsign.strip()
    mode3a = (record.get("070") or {}).get("Mode3A")
    if mode3a:
        raw_data["mode3a"] = mode3a

    return {
        "sensor_id": sensor_id,
        "sensor_type": "radar",
        "timestamp": timestamp.isoformat(),
        "azimuth_deg": azimuth_deg,
        "range_m": range_m,
        "altitude_m": altitude_m,
        "confidence": confidence,
        "raw_data": raw_data,
    }
