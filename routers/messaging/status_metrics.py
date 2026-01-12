from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .service import get_status_metrics as service_get_status_metrics

router = APIRouter(prefix="/status", tags=["Status Metrics"])


@router.get("/metrics")
async def get_status_metrics(
    db: Session = Depends(get_db), current_user = Depends(get_current_user)
):
    """
    Get comprehensive status metrics (admin-only).
    """
    return service_get_status_metrics(db, current_user=current_user)
