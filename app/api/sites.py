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

from app.auth import ROLE_ADMIN, Principal, require_role
from app.db import create_site, get_site_by_name, list_sites, record_audit
from app.models import Site

router = APIRouter()


@router.get("/sites", response_model=list[Site], dependencies=[Depends(require_role(ROLE_ADMIN))])
def get_sites() -> list[Site]:
    # Admin-only, deliberately not scoped by the caller's own site
    # (there's nothing to scope it by -- this lists every site in the
    # deployment, which is the whole point of the endpoint): a viewer/
    # operator key never needs the list of other sites that exist.
    return list_sites()


@router.post("/sites", response_model=Site, status_code=201)
def create_new_site(site: Site, principal: Principal = Depends(require_role(ROLE_ADMIN))) -> Site:
    if get_site_by_name(site.name) is not None:
        raise HTTPException(status_code=409, detail=f"Site '{site.name}' already exists")
    created = create_site(site.name)
    # No site_id of its own -- this action isn't scoped to the site it
    # created (see app/schema.py's audit_log.site_id docstring).
    record_audit(site_id=None, actor=principal.name, action="site.create", target=site.name)
    return created
