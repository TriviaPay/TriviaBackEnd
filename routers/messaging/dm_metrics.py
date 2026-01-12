import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user
from .service import ACTIVE_DM_SSE_CONNECTIONS

from .service import get_dm_metrics as service_get_dm_metrics

router = APIRouter(prefix="/dm", tags=["DM Metrics"])

_metrics_cache = {"ts": 0.0, "payload": None}


@router.get("/metrics")
def get_dm_metrics(
    db: Session = Depends(get_db), current_user = Depends(get_current_user)
):
    """
    Get comprehensive E2EE DM metrics (admin-only endpoint).

    Returns:
    - SSE connection stats
    - Redis status and lag
    - OTPK pool distribution
    - Message delivery stats
    - Device and bundle stats
    """
    now_ts = time.time()
    return service_get_dm_metrics(
        db,
        current_user=current_user,
        active_sse_connections=ACTIVE_DM_SSE_CONNECTIONS,
        now_ts=now_ts,
        cache=_metrics_cache,
    )
