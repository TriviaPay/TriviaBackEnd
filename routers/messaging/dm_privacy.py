from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .schemas import DMBlockUserRequest
from .service import (
    dm_block_user as service_dm_block_user,
    dm_list_blocks as service_dm_list_blocks,
    dm_unblock_user as service_dm_unblock_user,
)

router = APIRouter(prefix="/dm", tags=["DM Privacy"])


@router.post("/block")
def block_user(
    request: DMBlockUserRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Block a user. Prevents them from messaging you and seeing your key bundles.
    """
    return service_dm_block_user(
        db, current_user=current_user, blocked_user_id=request.blocked_user_id
    )


@router.delete("/block/{blocked_user_id}")
def unblock_user(
    blocked_user_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Unblock a user.
    """
    return service_dm_unblock_user(
        db, current_user=current_user, blocked_user_id=blocked_user_id
    )


@router.get("/blocks")
def list_blocks(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """
    List all users blocked by the current user.
    """
    return service_dm_list_blocks(
        db, current_user=current_user, limit=limit, offset=offset
    )
