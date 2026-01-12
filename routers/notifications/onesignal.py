from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .schemas import ListPlayersResponse, RegisterPlayerRequest
from .service import list_onesignal_players as service_list_onesignal_players
from .service import register_onesignal_player as service_register_onesignal_player

router = APIRouter(prefix="/onesignal", tags=["OneSignal"])


@router.post("/register")
def register_player(
    request: RegisterPlayerRequest,
    req: Request,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Register or update OneSignal player ID for push notifications"""
    ip = req.client.host if req.client else "unknown"
    return service_register_onesignal_player(
        db,
        current_user=current_user,
        ip=ip,
        player_id=request.player_id,
        platform=request.platform,
    )


@router.get("/players", response_model=ListPlayersResponse)
def list_players(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """List user's registered OneSignal players (for debugging)"""
    return service_list_onesignal_players(
        db, current_user=current_user, limit=limit, offset=offset
    )
