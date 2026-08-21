from fastapi import APIRouter, Depends

from app.auth import ROLE_ADMIN, Principal, require_role
from app.db import list_authorized_operators, upsert_authorized_operator
from app.models import AuthorizedOperator, AuthorizedOperatorInput

router = APIRouter()


@router.get("/authorized-operators", response_model=list[AuthorizedOperator])
def get_authorized_operators(
    principal: Principal = Depends(require_role(ROLE_ADMIN)),
) -> list[AuthorizedOperator]:
    # Admin-only, not just viewer: this list is exactly the set of
    # operator_id values that get a detection classified FRIENDLY (see
    # app/allowlist.py), so exposing it to lower roles would let them
    # read out the values needed to spoof that check.
    return [AuthorizedOperator(**row) for row in list_authorized_operators(principal.site_id)]


@router.put(
    "/authorized-operators/{operator_id}",
    response_model=AuthorizedOperator,
    status_code=201,
)
def register_authorized_operator(
    operator_id: str,
    body: AuthorizedOperatorInput,
    principal: Principal = Depends(require_role(ROLE_ADMIN)),
) -> AuthorizedOperator:
    upsert_authorized_operator(
        operator_id=operator_id,
        site_id=principal.site_id,
        name=body.name,
        public_key=body.public_key,
        active=body.active,
    )
    return AuthorizedOperator(
        operator_id=operator_id,
        site_id=principal.site_id,
        name=body.name,
        public_key=body.public_key,
        active=body.active,
    )
