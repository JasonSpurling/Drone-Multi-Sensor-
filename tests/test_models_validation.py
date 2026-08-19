import pytest
from pydantic import ValidationError

from app.models import Detection, SensorType


def make_kwargs(**overrides) -> dict:
    defaults = dict(sensor_id="radar-1", sensor_type=SensorType.RADAR, confidence=0.9)
    defaults.update(overrides)
    return defaults


def test_confidence_must_be_within_zero_and_one():
    Detection(**make_kwargs(confidence=0.0))
    Detection(**make_kwargs(confidence=1.0))
    with pytest.raises(ValidationError):
        Detection(**make_kwargs(confidence=1.1))
    with pytest.raises(ValidationError):
        Detection(**make_kwargs(confidence=-0.1))


def test_latitude_must_be_within_range():
    Detection(**make_kwargs(latitude=90.0, longitude=0.0))
    Detection(**make_kwargs(latitude=-90.0, longitude=0.0))
    with pytest.raises(ValidationError):
        Detection(**make_kwargs(latitude=90.1, longitude=0.0))


def test_longitude_must_be_within_range():
    Detection(**make_kwargs(latitude=0.0, longitude=180.0))
    with pytest.raises(ValidationError):
        Detection(**make_kwargs(latitude=0.0, longitude=-180.1))


def test_sensor_id_cannot_be_empty():
    with pytest.raises(ValidationError):
        Detection(**make_kwargs(sensor_id=""))


def test_sensor_id_max_length_enforced():
    with pytest.raises(ValidationError):
        Detection(**make_kwargs(sensor_id="x" * 101))
    Detection(**make_kwargs(sensor_id="x" * 100))


def test_invalid_sensor_type_rejected():
    with pytest.raises(ValidationError):
        Detection(sensor_id="s1", sensor_type="not-a-real-sensor", confidence=0.5)
