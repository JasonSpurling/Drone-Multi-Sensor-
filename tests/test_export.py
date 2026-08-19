import csv
import io
import xml.etree.ElementTree as ET
from datetime import datetime

from app.export import to_csv, to_gpx, to_kml
from app.models import Classification, Detection, SensorType, Track, TrackStatus

TRACK = Track(
    id=1, track_uid="abc-123",
    first_seen=datetime(2026, 1, 1, 12, 0, 0), last_seen=datetime(2026, 1, 1, 12, 0, 5),
    status=TrackStatus.ACTIVE, classification=Classification.DRONE,
)

DETECTIONS = [
    Detection(
        id=1, sensor_id="s1", sensor_type=SensorType.RADAR, timestamp=datetime(2026, 1, 1, 12, 0, 0),
        track_id=1, latitude=51.5, longitude=-0.1, altitude_m=100.0, confidence=0.9,
    ),
    Detection(
        id=2, sensor_id="s1", sensor_type=SensorType.RADAR, timestamp=datetime(2026, 1, 1, 12, 0, 5),
        track_id=1, latitude=51.501, longitude=-0.099, altitude_m=105.0, confidence=0.9,
    ),
]


def test_gpx_is_well_formed_xml_with_both_points():
    xml = to_gpx(TRACK, DETECTIONS)
    root = ET.fromstring(xml)
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    trkpts = root.findall(".//g:trkpt", ns)
    assert len(trkpts) == 2
    assert trkpts[0].attrib["lat"] == "51.5"
    assert trkpts[0].attrib["lon"] == "-0.1"


def test_kml_is_well_formed_xml_with_a_coordinate_per_point():
    xml = to_kml(TRACK, DETECTIONS)
    root = ET.fromstring(xml)
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    coords_text = root.find(".//k:coordinates", ns).text
    coords = coords_text.strip().split(" ")
    assert len(coords) == 2
    # KML coordinate order is lon,lat,alt -- opposite of GPX's lat/lon attrs.
    assert coords[0] == "-0.1,51.5,100.0"


def test_csv_has_a_header_row_and_one_row_per_detection():
    rows = list(csv.reader(io.StringIO(to_csv(DETECTIONS))))
    assert rows[0] == ["timestamp", "sensor_id", "sensor_type", "latitude", "longitude", "altitude_m", "confidence"]
    assert len(rows) == 3  # header + 2 detections
    assert rows[1][1] == "s1"


def test_gpx_and_kml_skip_detections_without_coordinates_but_csv_keeps_them():
    detections = DETECTIONS + [
        Detection(
            id=3, sensor_id="s1", sensor_type=SensorType.RADAR, timestamp=datetime(2026, 1, 1, 12, 0, 10),
            track_id=1, azimuth_deg=45.0, range_m=500.0, confidence=0.9,  # never georeferenced
        )
    ]
    gpx_root = ET.fromstring(to_gpx(TRACK, detections))
    kml_root = ET.fromstring(to_kml(TRACK, detections))
    assert len(gpx_root.findall(".//{http://www.topografix.com/GPX/1/1}trkpt")) == 2
    assert len(kml_root.find(".//{http://www.opengis.net/kml/2.2}coordinates").text.strip().split(" ")) == 2

    csv_rows = list(csv.reader(io.StringIO(to_csv(detections))))
    assert len(csv_rows) == 4  # header + all 3, including the ungeoreferenced one


def test_empty_detection_list_produces_valid_empty_documents():
    gpx_root = ET.fromstring(to_gpx(TRACK, []))
    kml_root = ET.fromstring(to_kml(TRACK, []))
    assert gpx_root.find(".//{http://www.topografix.com/GPX/1/1}trkpt") is None
    csv_rows = list(csv.reader(io.StringIO(to_csv([]))))
    assert len(csv_rows) == 1  # header only


def test_track_uid_and_classification_are_xml_escaped():
    # A track_uid with XML-special characters must not break the document.
    weird_track = Track(
        id=1, track_uid='<evil>&"stuff"</evil>',
        first_seen=datetime(2026, 1, 1), last_seen=datetime(2026, 1, 1),
        status=TrackStatus.ACTIVE, classification=Classification.DRONE,
    )
    gpx_root = ET.fromstring(to_gpx(weird_track, DETECTIONS))  # must not raise ParseError
    kml_root = ET.fromstring(to_kml(weird_track, DETECTIONS))
    ns_gpx = {"g": "http://www.topografix.com/GPX/1/1"}
    assert gpx_root.find(".//g:name", ns_gpx).text == '<evil>&"stuff"</evil>'
