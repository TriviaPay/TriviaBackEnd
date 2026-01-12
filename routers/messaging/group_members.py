from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .schemas import GroupAddMembersRequest, GroupBanRequest, GroupMuteRequest, GroupPromoteRequest
from .service import (
    add_group_members as service_add_group_members,
    ban_group_user as service_ban_group_user,
    demote_group_admin as service_demote_group_admin,
    leave_group as service_leave_group,
    list_group_members as service_list_group_members,
    mute_group as service_mute_group,
    promote_group_member as service_promote_group_member,
    remove_group_member as service_remove_group_member,
    unban_group_user as service_unban_group_user,
)

router = APIRouter(prefix="/groups", tags=["Group Members"])


@router.get("/{group_id}/members")
async def list_members(
    group_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """List group members with roles."""
    return service_list_group_members(db, current_user=current_user, group_id=group_id)


@router.post("/{group_id}/members")
async def add_members(
    group_id: str,
    request: GroupAddMembersRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Add members to group. Owner/admin only. Triggers rekey."""
    return service_add_group_members(
        db, current_user=current_user, group_id=group_id, request=request
    )


@router.delete("/{group_id}/members/{user_id}")
async def remove_member(
    group_id: str,
    user_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Remove member from group. Owner/admin only. Triggers rekey."""
    return service_remove_group_member(
        db, current_user=current_user, group_id=group_id, user_id=user_id
    )


@router.post("/{group_id}/leave")
async def leave_group(
    group_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Leave group. Triggers rekey."""
    return service_leave_group(db, current_user=current_user, group_id=group_id)


@router.post("/{group_id}/promote")
async def promote_member(
    group_id: str,
    request: GroupPromoteRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Promote member to admin. Owner/admin only."""
    return service_promote_group_member(
        db, current_user=current_user, group_id=group_id, user_id=request.user_id
    )


@router.post("/{group_id}/demote")
async def demote_admin(
    group_id: str,
    request: GroupPromoteRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Demote admin to member. Owner only."""
    return service_demote_group_admin(
        db, current_user=current_user, group_id=group_id, user_id=request.user_id
    )


@router.post("/{group_id}/ban")
async def ban_user(
    group_id: str,
    request: GroupBanRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Ban user from group. Owner/admin only. Triggers rekey."""
    return service_ban_group_user(
        db, current_user=current_user, group_id=group_id, request=request
    )


@router.delete("/{group_id}/ban/{user_id}")
async def unban_user(
    group_id: str,
    user_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Unban user. Owner/admin only."""
    return service_unban_group_user(
        db, current_user=current_user, group_id=group_id, user_id=user_id
    )


@router.post("/{group_id}/mute")
async def mute_group(
    group_id: str,
    request: GroupMuteRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Mute group notifications. None = unmute."""
    return service_mute_group(
        db, current_user=current_user, group_id=group_id, request=request
    )
