from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, Principal, require_role
from app.db import create_zone, get_zone, get_zone_by_name, list_zones, record_audit, update_zone
from app.models import Zone, ZoneInput

router = APIRouter()


@router.get("/zones", response_model=list[Zone])
def get_zones(
    include_inactive: bool = Query(
        default=False,
        description="Include deactivated zones too -- off by default, since map/incident-check "
        "callers only ever care about active ones; the zone-management UI sets this so a "
        "deactivated zone can still be found and reactivated.",
    ),
    principal: Principal = Depends(require_role(ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN)),
) -> list[Zone]:
    return list_zones(site_id=principal.site_id, active_only=not include_inactive)


@router.post("/zones", response_model=Zone, status_code=201)
def create_new_zone(body: ZoneInput, principal: Principal = Depends(require_role(ROLE_ADMIN))) -> Zone:
    # Same reasoning as app/api/sites.py's create_new_site: a name collision
    # here is a caller mistake (probably meant to edit the existing zone
    # via PUT), not a valid way to have two zones share a name within one
    # site.
    if get_zone_by_name(body.name, principal.site_id) is not None:
        raise HTTPException(status_code=409, detail=f"Zone '{body.name}' already exists")
    created = create_zone(
        Zone(
            site_id=principal.site_id,
            name=body.name,
            zone_type=body.zone_type,
            polygon=body.polygon,
            min_altitude_m=body.min_altitude_m,
            max_altitude_m=body.max_altitude_m,
            active=body.active,
        )
    )
    record_audit(site_id=principal.site_id, actor=principal.name, action="zone.create", target=body.name)
    return created


@router.put("/zones/{zone_id}", response_model=Zone)
def edit_zone(
    zone_id: int, body: ZoneInput, principal: Principal = Depends(require_role(ROLE_ADMIN))
) -> Zone:
    existing = get_zone(zone_id, principal.site_id)
    if existing is None:
        # Not a 403 (which would confirm a zone with this id exists in
        # some other site) -- the same 404-not-403 pattern every other
        # site-scoped lookup in this app follows.
        raise HTTPException(status_code=404, detail="Zone not found")
    updated = update_zone(
        Zone(
            id=zone_id,
            site_id=principal.site_id,
            name=body.name,
            zone_type=body.zone_type,
            polygon=body.polygon,
            min_altitude_m=body.min_altitude_m,
            max_altitude_m=body.max_altitude_m,
            active=body.active,
        )
    )
    record_audit(site_id=principal.site_id, actor=principal.name, action="zone.update", target=str(zone_id))
    return updated
