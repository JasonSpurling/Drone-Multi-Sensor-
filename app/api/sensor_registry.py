from fastapi import APIRouter, Depends

from app.auth import ROLE_ADMIN, require_role
from app.db import list_sensor_registrations, upsert_sensor_registration
from app.models import SensorRegistration, SensorRegistrationInput

router = APIRouter()


@router.get("/sensor-registrations", response_model=list[SensorRegistration])
def get_sensor_registrations() -> list[SensorRegistration]:
    return [SensorRegistration(**row) for row in list_sensor_registrations()]


@router.put(
    "/sensor-registrations/{sensor_id}",
    response_model=SensorRegistration,
    status_code=201,
    dependencies=[Depends(require_role(ROLE_ADMIN))],
)
def register_sensor(sensor_id: str, registration: SensorRegistrationInput) -> SensorRegistration:
    upsert_sensor_registration(
        sensor_id=sensor_id,
        sensor_type=registration.sensor_type.value,
        latitude=registration.latitude,
        longitude=registration.longitude,
        altitude_m=registration.altitude_m,
        azimuth_reference_deg=registration.azimuth_reference_deg,
        active=registration.active,
    )
    return SensorRegistration(sensor_id=sensor_id, **registration.model_dump())
