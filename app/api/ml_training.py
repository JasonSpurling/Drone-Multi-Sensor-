from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.auth import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, Principal, require_role
from app.db import list_labeled_detections
from app.export import labeled_detections_to_training_csv

router = APIRouter()
_viewer_roles = (ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN)


@router.get("/ml/training-data/export")
def export_training_data(
    principal: Principal = Depends(require_role(*_viewer_roles)),
) -> Response:
    """Every operator-labeled detection (PUT /api/detections/{id}/label)
    in this site, as a CSV in exactly the shape app/ml/train.py's --csv
    expects -- the bridge between real labeled traffic accumulating over
    time and actually training a model from it.
    """
    detections = list_labeled_detections(principal.site_id)
    body = labeled_detections_to_training_csv(detections)
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="ml-training-data.csv"'},
    )
