from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .schemas import GroupCreateRequest, GroupUpdateRequest
from .service import (
    create_group as service_create_group,
    delete_group as service_delete_group,
    get_group as service_get_group,
    list_groups as service_list_groups,
    update_group as service_update_group,
)

router = APIRouter(prefix="/groups", tags=["Groups"])


@router.post("")
def create_group(
    request: GroupCreateRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Create a new group. Creator becomes owner."""
    return service_create_group(db, current_user=current_user, request=request)


@router.get("")
def list_groups(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """List groups the user is a member of, ordered by last message time."""
    return service_list_groups(
        db, current_user=current_user, limit=limit, offset=offset
    )


@router.get("/{group_id}")
def get_group(
    group_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Get group details including participant count and user's role."""
    return service_get_group(db, current_user=current_user, group_id=group_id)


@router.patch("/{group_id}")
def update_group(
    group_id: str,
    request: GroupUpdateRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Update group title, about, or photo. Owner/admin only."""
    return service_update_group(
        db, current_user=current_user, group_id=group_id, request=request
    )


@router.delete("/{group_id}")
def delete_group(
    group_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Close/archive a group. Owner only."""
    return service_delete_group(db, current_user=current_user, group_id=group_id)
