from fastapi.testclient import TestClient

from app.main import app


def test_sensor_registration_round_trip(isolated_db):
    with TestClient(app) as client:
        body = {
            "sensor_type": "radar",
            "latitude": 51.5,
            "longitude": -0.1,
            "altitude_m": 15.0,
            "azimuth_reference_deg": 90.0,
        }
        put = client.put("/api/sensor-registrations/radar-1", json=body)
        assert put.status_code == 201
        assert put.json()["sensor_id"] == "radar-1"

        listed = client.get("/api/sensor-registrations").json()
        assert len(listed) == 1
        assert listed[0]["sensor_id"] == "radar-1"
        assert listed[0]["azimuth_reference_deg"] == 90.0


def test_sensor_registration_upsert_overwrites():
    with TestClient(app) as client:
        base = {"sensor_type": "radar", "latitude": 51.5, "longitude": -0.1, "azimuth_reference_deg": 0}
        client.put("/api/sensor-registrations/radar-1", json=base)
        updated = {**base, "azimuth_reference_deg": 180.0}
        client.put("/api/sensor-registrations/radar-1", json=updated)

        listed = client.get("/api/sensor-registrations").json()
        assert len(listed) == 1  # upsert, not a new row
        assert listed[0]["azimuth_reference_deg"] == 180.0


def test_authorized_operator_round_trip():
    with TestClient(app) as client:
        put = client.put("/api/authorized-operators/OP-1", json={"name": "Acme Surveying"})
        assert put.status_code == 201

        listed = client.get("/api/authorized-operators").json()
        assert len(listed) == 1
        assert listed[0]["operator_id"] == "OP-1"
        assert listed[0]["name"] == "Acme Surveying"
        assert listed[0]["active"] is True


def test_authorized_operator_can_be_deactivated():
    with TestClient(app) as client:
        client.put("/api/authorized-operators/OP-1", json={"name": "Acme Surveying"})
        client.put("/api/authorized-operators/OP-1", json={"name": "Acme Surveying", "active": False})

        listed = client.get("/api/authorized-operators").json()
        assert listed[0]["active"] is False


def test_invalid_sensor_registration_rejected():
    with TestClient(app) as client:
        bad_body = {"sensor_type": "radar", "latitude": 999.0, "longitude": -0.1, "azimuth_reference_deg": 0}
        r = client.put("/api/sensor-registrations/radar-1", json=bad_body)
        assert r.status_code == 422
