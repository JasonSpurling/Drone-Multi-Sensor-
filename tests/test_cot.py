import xml.etree.ElementTree as ET
from datetime import datetime

import pytest

from app.cot import build_cot_xml, classification_to_cot_type
from app.models import Classification, Track, TrackStatus

TRACK = Track(
    id=1, track_uid="abc-123-def",
    first_seen=datetime(2026, 1, 1), last_seen=datetime(2026, 1, 1),
    status=TrackStatus.ACTIVE, classification=Classification.DRONE,
    latitude=51.5, longitude=-0.1, altitude_m=120.0,
)


def test_drone_is_unknown_affiliation_never_hostile():
    # A sensor-fusion pipeline must never auto-declare hostile -- that's
    # a human PID decision.
    assert classification_to_cot_type(Classification.DRONE) == "a-u-A"


def test_friendly_and_aircraft_map_to_friendly_affiliation():
    assert classification_to_cot_type(Classification.FRIENDLY) == "a-f-A"
    assert classification_to_cot_type(Classification.AIRCRAFT) == "a-f-A"


def test_bird_maps_to_neutral():
    assert classification_to_cot_type(Classification.BIRD) == "a-n-A"


def test_unknown_classification_maps_to_unknown_affiliation():
    assert classification_to_cot_type(Classification.UNKNOWN) == "a-u-A"


def test_build_cot_xml_is_well_formed_with_correct_position():
    xml_bytes = build_cot_xml(TRACK)
    root = ET.fromstring(xml_bytes)
    assert root.tag == "event"
    assert root.attrib["type"] == "a-u-A"
    assert root.attrib["uid"] == "drone-multi-sensor.abc-123-def"

    point = root.find("point")
    assert point.attrib["lat"] == "51.5"
    assert point.attrib["lon"] == "-0.1"
    assert point.attrib["hae"] == "120.0"


def test_build_cot_xml_returns_none_without_a_resolved_position():
    track = TRACK.model_copy(update={"latitude": None, "longitude": None})
    assert build_cot_xml(track) is None


def test_stale_seconds_is_configurable():
    xml_bytes = build_cot_xml(TRACK, stale_seconds=10.0)
    root = ET.fromstring(xml_bytes)

    start = datetime.fromisoformat(root.attrib["start"].replace("Z", "+00:00"))
    stale = datetime.fromisoformat(root.attrib["stale"].replace("Z", "+00:00"))
    assert (stale - start).total_seconds() == pytest.approx(10.0, abs=1.0)


def test_position_uncertainty_becomes_circular_error():
    track = TRACK.model_copy(update={"position_uncertainty_m": 42.0})
    root = ET.fromstring(build_cot_xml(track))
    assert root.find("point").attrib["ce"] == "42.0"


def test_missing_altitude_and_uncertainty_use_cot_unknown_sentinel():
    track = TRACK.model_copy(update={"altitude_m": None, "position_uncertainty_m": None})
    root = ET.fromstring(build_cot_xml(track))
    point = root.find("point")
    assert point.attrib["hae"] == "9999999.0"
    assert point.attrib["ce"] == "9999999.0"


def test_callsign_is_derived_from_track_uid():
    root = ET.fromstring(build_cot_xml(TRACK))
    contact = root.find("detail/contact")
    assert contact.attrib["callsign"] == TRACK.track_uid[:8]
