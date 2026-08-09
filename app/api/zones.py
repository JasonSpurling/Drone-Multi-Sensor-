from fastapi import APIRouter

from app.db import list_zones
from app.models import Zone

router = APIRouter()


@router.get("/zones", response_model=list[Zone])
def get_zones() -> list[Zone]:
    return list_zones(active_only=True)
