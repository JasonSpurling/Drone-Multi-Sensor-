from datetime import datetime

from app.adapters.lattice import ENTITY_EXPIRY_SECONDS, track_to_publish_entity_kwargs
from app.models import Classification, Track, TrackStatus

NOW = datetime(2026, 1, 1, 12, 0, 0)


def _track(**overrides) -> Track:
    defaults = {
        "id": 1, "track_uid": "trk-abc123", "first_seen": NOW, "last_seen": NOW,
        "status": TrackStatus.ACTIVE, "classification": Classification.UNKNOWN,
        "latitude": 51.5, "longitude": -0.1,
    }
    defaults.update(overrides)
    return Track(**defaults)


def test_track_with_no_position_yet_is_skipped():
    track = _track(latitude=None, longitude=None)
    assert track_to_publish_entity_kwargs(track, now=NOW) is None


def test_entity_id_is_derived_from_track_uid():
    kwargs = track_to_publish_entity_kwargs(_track(), now=NOW)
    assert kwargs["entity_id"] == "drone-multi-sensor-trk-abc123"


def test_active_track_is_live():
    kwargs = track_to_publish_entity_kwargs(_track(status=TrackStatus.ACTIVE), now=NOW)
    assert kwargs["is_live"] is True


def test_lost_track_is_not_live():
    kwargs = track_to_publish_entity_kwargs(_track(status=TrackStatus.LOST), now=NOW)
    assert kwargs["is_live"] is False


def test_expiry_time_is_offset_from_now():
    kwargs = track_to_publish_entity_kwargs(_track(), now=NOW)
    assert (kwargs["expiry_time"] - NOW).total_seconds() == ENTITY_EXPIRY_SECONDS


def test_position_maps_lat_lon():
    kwargs = track_to_publish_entity_kwargs(_track(latitude=51.5, longitude=-0.1), now=NOW)
    position = kwargs["location"]["position"]
    assert position["latitude_degrees"] == 51.5
    assert position["longitude_degrees"] == -0.1


def test_altitude_omitted_when_unknown():
    kwargs = track_to_publish_entity_kwargs(_track(altitude_m=None), now=NOW)
    assert "altitude_hae_meters" not in kwargs["location"]["position"]


def test_altitude_included_when_known():
    kwargs = track_to_publish_entity_kwargs(_track(altitude_m=120.0), now=NOW)
    assert kwargs["location"]["position"]["altitude_hae_meters"] == 120.0


def test_speed_omitted_when_unknown():
    kwargs = track_to_publish_entity_kwargs(_track(speed_mps=None), now=NOW)
    assert "speed_mps" not in kwargs["location"]


def test_speed_included_when_known():
    kwargs = track_to_publish_entity_kwargs(_track(speed_mps=12.5), now=NOW)
    assert kwargs["location"]["speed_mps"] == 12.5


def test_friendly_classification_maps_to_friendly_disposition():
    kwargs = track_to_publish_entity_kwargs(_track(classification=Classification.FRIENDLY), now=NOW)
    assert kwargs["mil_view"]["disposition"] == "DISPOSITION_FRIENDLY"


def test_drone_classification_maps_to_suspicious_not_hostile():
    # This app never makes an intent judgment -- SUSPICIOUS ("warrants
    # attention"), not HOSTILE, is the honest ceiling for what it knows.
    kwargs = track_to_publish_entity_kwargs(_track(classification=Classification.DRONE), now=NOW)
    assert kwargs["mil_view"]["disposition"] == "DISPOSITION_SUSPICIOUS"


def test_unknown_classification_maps_to_unknown_disposition():
    kwargs = track_to_publish_entity_kwargs(_track(classification=Classification.UNKNOWN), now=NOW)
    assert kwargs["mil_view"]["disposition"] == "DISPOSITION_UNKNOWN"


def test_bird_and_aircraft_map_to_neutral():
    for classification in (Classification.BIRD, Classification.AIRCRAFT):
        kwargs = track_to_publish_entity_kwargs(_track(classification=classification), now=NOW)
        assert kwargs["mil_view"]["disposition"] == "DISPOSITION_NEUTRAL"


def test_environment_is_air():
    kwargs = track_to_publish_entity_kwargs(_track(), now=NOW)
    assert kwargs["mil_view"]["environment"] == "ENVIRONMENT_AIR"


def test_provenance_carries_track_uid_and_last_seen():
    kwargs = track_to_publish_entity_kwargs(_track(track_uid="trk-xyz", last_seen=NOW), now=NOW)
    assert kwargs["provenance"]["source_id"] == "trk-xyz"
    assert kwargs["provenance"]["source_update_time"] == NOW


def test_integration_name_is_configurable():
    kwargs = track_to_publish_entity_kwargs(_track(), integration_name="my-deployment", now=NOW)
    assert kwargs["provenance"]["integration_name"] == "my-deployment"


def test_ontology_template_is_track():
    kwargs = track_to_publish_entity_kwargs(_track(), now=NOW)
    assert kwargs["ontology"]["template"] == "TEMPLATE_TRACK"
