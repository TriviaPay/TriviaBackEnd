import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .service import get_group_metrics as service_get_group_metrics

router = APIRouter(prefix="/groups", tags=["Group Metrics"])

_metrics_cache = {"ts": 0.0, "payload": None}


@router.get("/metrics")
def get_group_metrics(
    db: Session = Depends(get_db), current_user = Depends(get_current_user)
):
    """
    Get comprehensive group metrics (admin-only).
    """
    now_ts = time.time()
    return service_get_group_metrics(
        db, current_user=current_user, now_ts=now_ts, cache=_metrics_cache
    )
