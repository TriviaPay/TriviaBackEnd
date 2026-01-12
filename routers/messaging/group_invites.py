from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .schemas import GroupInviteCreateRequest, GroupJoinRequest
from .service import (
    create_group_invite as service_create_group_invite,
    join_group_by_invite as service_join_group_by_invite,
    list_group_invites as service_list_group_invites,
    revoke_group_invite as service_revoke_group_invite,
)

router = APIRouter(prefix="/groups", tags=["Group Invites"])


@router.post("/{group_id}/invites")
async def create_invite(
    group_id: str,
    request: GroupInviteCreateRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Create invite link/code. Owner/admin only."""
    return service_create_group_invite(
        db, current_user=current_user, group_id=group_id, request=request
    )


@router.get("/{group_id}/invites")
async def list_invites(
    group_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """List active invites. Owner/admin only."""
    return service_list_group_invites(
        db, current_user=current_user, group_id=group_id
    )


@router.delete("/{group_id}/invites/{invite_id}")
async def revoke_invite(
    group_id: str,
    invite_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Revoke invite. Owner/admin only."""
    return service_revoke_group_invite(
        db, current_user=current_user, group_id=group_id, invite_id=invite_id
    )


@router.post("/join")
async def join_group(
    request: GroupJoinRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Join group via invite code. Validates capacity, bans. Triggers rekey."""
    return service_join_group_by_invite(
        db, current_user=current_user, request=request
    )
