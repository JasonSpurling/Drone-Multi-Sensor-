"""Site management: creating additional sites and listing every site in
the deployment. Not site-scoped like every other router in app/api/ --
these operate on the site catalog itself, not one site's data.

KNOWN LIMITATION: any admin-role key can manage every site's existence
(list/create), not just the site it's scoped to -- there's no separate
"deployment superadmin" vs. "site admin" distinction in this version's
role model (see app/auth.py's ROLE_ADMIN). Fine for an operator who
controls all of a deployment's keys; a deployment that wants to hand a
site's admin key to a different, less-trusted party than the deployment
owner would need that distinction added first.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.auth import ROLE_ADMIN, require_role
from app.db import create_site, get_site_by_name, list_sites
from app.models import Site

router = APIRouter()


@router.get("/sites", response_model=list[Site], dependencies=[Depends(require_role(ROLE_ADMIN))])
def get_sites() -> list[Site]:
    # Admin-only, deliberately not scoped by the caller's own site
    # (there's nothing to scope it by -- this lists every site in the
    # deployment, which is the whole point of the endpoint): a viewer/
    # operator key never needs the list of other sites that exist.
    return list_sites()


@router.post("/sites", response_model=Site, status_code=201, dependencies=[Depends(require_role(ROLE_ADMIN))])
def create_new_site(site: Site) -> Site:
    if get_site_by_name(site.name) is not None:
        raise HTTPException(status_code=409, detail=f"Site '{site.name}' already exists")
    return create_site(site.name)
