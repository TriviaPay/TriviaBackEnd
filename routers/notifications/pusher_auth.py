from fastapi import APIRouter, Depends, Form
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .service import pusher_authenticate as service_pusher_authenticate

router = APIRouter(prefix="/pusher", tags=["Pusher"])


@router.post("/auth")
async def pusher_auth(
    socket_id: str = Form(...),
    channel_name: str = Form(...),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Authenticate Pusher channel subscription."""
    return service_pusher_authenticate(
        db, current_user=current_user, socket_id=socket_id, channel_name=channel_name
    )
