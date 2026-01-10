from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db import get_db
from models import User
from routers.dependencies import get_current_user

from .schemas import UpdatePresenceRequest
from .service import get_my_presence as service_get_my_presence
from .service import update_my_presence as service_update_my_presence

router = APIRouter(prefix="/presence", tags=["Presence"])


@router.get("")
async def get_my_presence(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """Get my presence settings."""
    return service_get_my_presence(db, current_user=current_user)


@router.patch("")
async def update_presence(
    request: UpdatePresenceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update privacy settings."""
    return service_update_my_presence(db, current_user=current_user, request=request)
