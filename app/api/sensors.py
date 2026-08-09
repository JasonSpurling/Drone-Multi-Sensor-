from fastapi import APIRouter

from app.models import SensorHealth
from app.sensors import get_sensor_health

router = APIRouter()


@router.get("/sensors", response_model=list[SensorHealth])
def get_sensors() -> list[SensorHealth]:
    return get_sensor_health()
