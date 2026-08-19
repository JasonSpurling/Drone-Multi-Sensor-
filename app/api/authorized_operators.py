from fastapi import APIRouter, Depends

from app.auth import ROLE_ADMIN, require_role
from app.db import list_authorized_operators, upsert_authorized_operator
from app.models import AuthorizedOperator, AuthorizedOperatorInput

router = APIRouter()


@router.get(
    "/authorized-operators",
    response_model=list[AuthorizedOperator],
    dependencies=[Depends(require_role(ROLE_ADMIN))],
)
def get_authorized_operators() -> list[AuthorizedOperator]:
    # Admin-only, not just viewer: this list is exactly the set of
    # operator_id values that get a detection classified FRIENDLY (see
    # app/allowlist.py), so exposing it to lower roles would let them
    # read out the values needed to spoof that check.
    return [AuthorizedOperator(**row) for row in list_authorized_operators()]


@router.put(
    "/authorized-operators/{operator_id}",
    response_model=AuthorizedOperator,
    status_code=201,
    dependencies=[Depends(require_role(ROLE_ADMIN))],
)
def register_authorized_operator(
    operator_id: str, body: AuthorizedOperatorInput
) -> AuthorizedOperator:
    upsert_authorized_operator(operator_id=operator_id, name=body.name, active=body.active)
    return AuthorizedOperator(operator_id=operator_id, name=body.name, active=body.active)
