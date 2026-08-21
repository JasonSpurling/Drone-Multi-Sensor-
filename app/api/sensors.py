from fastapi import APIRouter, Depends

from app.auth import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, Principal, require_role
from app.models import SensorHealth
from app.sensors import get_sensor_health

router = APIRouter()


@router.get("/sensors", response_model=list[SensorHealth])
def get_sensors(
    principal: Principal = Depends(require_role(ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN)),
) -> list[SensorHealth]:
    return get_sensor_health(principal.site_id)
