from fastapi import APIRouter, Depends

from app.auth import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, Principal, require_role
from app.db import list_sensor_registrations, record_audit, upsert_sensor_registration
from app.models import SensorRegistration, SensorRegistrationInput

router = APIRouter()


@router.get("/sensor-registrations", response_model=list[SensorRegistration])
def get_sensor_registrations(
    principal: Principal = Depends(require_role(ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN)),
) -> list[SensorRegistration]:
    return [SensorRegistration(**row) for row in list_sensor_registrations(principal.site_id)]


@router.put(
    "/sensor-registrations/{sensor_id}",
    response_model=SensorRegistration,
    status_code=201,
)
def register_sensor(
    sensor_id: str,
    registration: SensorRegistrationInput,
    principal: Principal = Depends(require_role(ROLE_ADMIN)),
) -> SensorRegistration:
    upsert_sensor_registration(
        sensor_id=sensor_id,
        site_id=principal.site_id,
        sensor_type=registration.sensor_type.value,
        latitude=registration.latitude,
        longitude=registration.longitude,
        altitude_m=registration.altitude_m,
        azimuth_reference_deg=registration.azimuth_reference_deg,
        active=registration.active,
    )
    record_audit(
        site_id=principal.site_id, actor=principal.name, action="sensor.register", target=sensor_id
    )
    return SensorRegistration(sensor_id=sensor_id, site_id=principal.site_id, **registration.model_dump())
