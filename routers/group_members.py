from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Optional
import uuid
import logging

from db import get_db
from models import User, Group, GroupParticipant, GroupBan, E2EEDevice
from routers.dependencies import get_current_user
from config import GROUPS_ENABLED, GROUP_MAX_PARTICIPANTS
from utils.redis_pubsub import publish_group_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/groups", tags=["Group Members"])


class AddMembersRequest(BaseModel):
    user_ids: List[int] = Field(..., min_items=1, example=[1142961859, 9876543210])
    
    class Config:
        json_schema_extra = {
            "example": {
                "user_ids": [1142961859, 9876543210]
            }
        }


class PromoteRequest(BaseModel):
    user_id: int = Field(..., example=1142961859)
    
    class Config:
        json_schema_extra = {
            "example": {
                "user_id": 1142961859
            }
        }


class BanRequest(BaseModel):
    user_id: int = Field(..., example=1142961859)
    reason: Optional[str] = Field(None, example="Violation of group rules")
    
    class Config:
        json_schema_extra = {
            "example": {
                "user_id": 1142961859,
                "reason": "Violation of group rules"
            }
        }


class MuteRequest(BaseModel):
    mute_until: Optional[datetime] = Field(None, example="2025-11-10T16:00:00Z")  # None = unmute
    
    class Config:
        json_schema_extra = {
            "example": {
                "mute_until": "2025-11-10T16:00:00Z"
            }
        }


def check_group_role(db: Session, group_id: uuid.UUID, user_id: int, required_roles: List[str]) -> GroupParticipant:
    """Check if user has required role in group."""
    participant = db.query(GroupParticipant).filter(
        GroupParticipant.group_id == group_id,
        GroupParticipant.user_id == user_id
    ).first()
    
    if not participant or participant.is_banned:
        raise HTTPException(status_code=403, detail="NOT_MEMBER")
    
    if participant.role not in required_roles:
        raise HTTPException(status_code=403, detail="FORBIDDEN")
    
    return participant


def increment_group_epoch(db: Session, group: Group) -> None:
    """Increment group epoch and publish epoch_changed event."""
    group.group_epoch += 1
    group.updated_at = datetime.utcnow()
    
    # Publish epoch_changed event
    publish_group_message(str(group.id), {
        "type": "epoch_changed",
        "group_id": str(group.id),
        "new_epoch": group.group_epoch
    })


@router.get("/{group_id}/members")
async def list_members(
    group_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List group members with roles."""
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")
    
    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid group ID format")
    
    # Check membership
    check_group_role(db, group_uuid, current_user.account_id, ['owner', 'admin', 'member'])
    
    participants = db.query(GroupParticipant).filter(
        GroupParticipant.group_id == group_uuid,
        GroupParticipant.is_banned == False
    ).all()
    
    return {
        "members": [
            {
                "user_id": p.user_id,
                "role": p.role,
                "joined_at": p.joined_at.isoformat() if p.joined_at else None
            }
            for p in participants
        ]
    }


@router.post("/{group_id}/members")
async def add_members(
    group_id: str,
    request: AddMembersRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Add members to group. Owner/admin only. Triggers rekey."""
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")
    
    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid group ID format")
    
    group = db.query(Group).filter(Group.id == group_uuid).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    if group.is_closed:
        raise HTTPException(status_code=403, detail="Group is closed")
    
    # Check permissions
    check_group_role(db, group_uuid, current_user.account_id, ['owner', 'admin'])
    
    # Check current participant count
    current_count = db.query(GroupParticipant).filter(
        GroupParticipant.group_id == group_uuid,
        GroupParticipant.is_banned == False
    ).count()
    
    if current_count + len(request.user_ids) > GROUP_MAX_PARTICIPANTS:
        raise HTTPException(status_code=409, detail="GROUP_FULL")
    
    # Validate users exist and not already members
    added_users = []
    for user_id in request.user_ids:
        # Check if user exists
        user = db.query(User).filter(User.account_id == user_id).first()
        if not user:
            continue
        
        # Check if already a member
        existing = db.query(GroupParticipant).filter(
            GroupParticipant.group_id == group_uuid,
            GroupParticipant.user_id == user_id
        ).first()
        
        if existing:
            if existing.is_banned:
                # Unban and reactivate
                existing.is_banned = False
                existing.role = 'member'
                existing.joined_at = datetime.utcnow()
            else:
                continue  # Already a member
        
        # Check if banned
        ban = db.query(GroupBan).filter(
            GroupBan.group_id == group_uuid,
            GroupBan.user_id == user_id
        ).first()
        if ban:
            continue  # Skip banned users
        
        # Add participant
        participant = GroupParticipant(
            group_id=group_uuid,
            user_id=user_id,
            role='member',
            joined_at=datetime.utcnow()
        )
        db.add(participant)
        added_users.append(user_id)
    
    if added_users:
        # Increment epoch (triggers rekey)
        increment_group_epoch(db, group)
        
        try:
            db.commit()
            return {"added_user_ids": added_users, "new_epoch": group.group_epoch}
        except Exception as e:
            db.rollback()
            logger.error(f"Error adding members: {e}")
            raise HTTPException(status_code=500, detail="Failed to add members")
    
    return {"added_user_ids": [], "message": "No new members added"}


@router.delete("/{group_id}/members/{user_id}")
async def remove_member(
    group_id: str,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Remove member from group. Owner/admin only. Triggers rekey."""
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")
    
    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid group ID format")
    
    group = db.query(Group).filter(Group.id == group_uuid).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    # Check permissions
    check_group_role(db, group_uuid, current_user.account_id, ['owner', 'admin'])
    
    # Can't remove owner
    target_participant = db.query(GroupParticipant).filter(
        GroupParticipant.group_id == group_uuid,
        GroupParticipant.user_id == user_id
    ).first()
    
    if not target_participant:
        raise HTTPException(status_code=404, detail="User is not a member")
    
    if target_participant.role == 'owner':
        raise HTTPException(status_code=403, detail="Cannot remove owner")
    
    # Remove participant
    db.delete(target_participant)
    
    # Increment epoch (triggers rekey)
    increment_group_epoch(db, group)
    
    try:
        db.commit()
        return {"message": "Member removed", "new_epoch": group.group_epoch}
    except Exception as e:
        db.rollback()
        logger.error(f"Error removing member: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove member")


@router.post("/{group_id}/leave")
async def leave_group(
    group_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Leave group. Triggers rekey."""
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")
    
    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid group ID format")
    
    group = db.query(Group).filter(Group.id == group_uuid).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    participant = db.query(GroupParticipant).filter(
        GroupParticipant.group_id == group_uuid,
        GroupParticipant.user_id == current_user.account_id
    ).first()
    
    if not participant:
        raise HTTPException(status_code=403, detail="Not a member")
    
    # Owner can't leave (must transfer ownership or close group)
    if participant.role == 'owner':
        raise HTTPException(status_code=403, detail="Owner cannot leave. Transfer ownership or close group.")
    
    # Remove participant
    db.delete(participant)
    
    # Increment epoch (triggers rekey)
    increment_group_epoch(db, group)
    
    try:
        db.commit()
        return {"message": "Left group", "new_epoch": group.group_epoch}
    except Exception as e:
        db.rollback()
        logger.error(f"Error leaving group: {e}")
        raise HTTPException(status_code=500, detail="Failed to leave group")


@router.post("/{group_id}/promote")
async def promote_member(
    group_id: str,
    request: PromoteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Promote member to admin. Owner/admin only."""
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")
    
    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid group ID format")
    
    # Check permissions
    check_group_role(db, group_uuid, current_user.account_id, ['owner', 'admin'])
    
    target_participant = db.query(GroupParticipant).filter(
        GroupParticipant.group_id == group_uuid,
        GroupParticipant.user_id == request.user_id
    ).first()
    
    if not target_participant or target_participant.is_banned:
        raise HTTPException(status_code=404, detail="User is not a member")
    
    if target_participant.role == 'admin':
        return {"message": "User is already an admin"}
    
    target_participant.role = 'admin'
    
    try:
        db.commit()
        return {"message": "Member promoted to admin"}
    except Exception as e:
        db.rollback()
        logger.error(f"Error promoting member: {e}")
        raise HTTPException(status_code=500, detail="Failed to promote member")


@router.post("/{group_id}/demote")
async def demote_admin(
    group_id: str,
    request: PromoteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Demote admin to member. Owner only."""
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")
    
    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid group ID format")
    
    # Check permissions (owner only)
    check_group_role(db, group_uuid, current_user.account_id, ['owner'])
    
    target_participant = db.query(GroupParticipant).filter(
        GroupParticipant.group_id == group_uuid,
        GroupParticipant.user_id == request.user_id
    ).first()
    
    if not target_participant or target_participant.is_banned:
        raise HTTPException(status_code=404, detail="User is not a member")
    
    if target_participant.role != 'admin':
        return {"message": "User is not an admin"}
    
    target_participant.role = 'member'
    
    try:
        db.commit()
        return {"message": "Admin demoted to member"}
    except Exception as e:
        db.rollback()
        logger.error(f"Error demoting admin: {e}")
        raise HTTPException(status_code=500, detail="Failed to demote admin")


@router.post("/{group_id}/ban")
async def ban_user(
    group_id: str,
    request: BanRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Ban user from group. Owner/admin only. Triggers rekey."""
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")
    
    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid group ID format")
    
    group = db.query(Group).filter(Group.id == group_uuid).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    # Check permissions
    check_group_role(db, group_uuid, current_user.account_id, ['owner', 'admin'])
    
    # Can't ban owner
    target_participant = db.query(GroupParticipant).filter(
        GroupParticipant.group_id == group_uuid,
        GroupParticipant.user_id == request.user_id
    ).first()
    
    if target_participant and target_participant.role == 'owner':
        raise HTTPException(status_code=403, detail="Cannot ban owner")
    
    # Mark participant as banned
    if target_participant:
        target_participant.is_banned = True
    else:
        # Create participant record as banned
        target_participant = GroupParticipant(
            group_id=group_uuid,
            user_id=request.user_id,
            role='member',
            is_banned=True
        )
        db.add(target_participant)
    
    # Add to bans table
    ban = db.query(GroupBan).filter(
        GroupBan.group_id == group_uuid,
        GroupBan.user_id == request.user_id
    ).first()
    
    if not ban:
        ban = GroupBan(
            group_id=group_uuid,
            user_id=request.user_id,
            banned_by=current_user.account_id,
            reason=request.reason,
            banned_at=datetime.utcnow()
        )
        db.add(ban)
    
    # Increment epoch (triggers rekey)
    increment_group_epoch(db, group)
    
    try:
        db.commit()
        return {"message": "User banned", "new_epoch": group.group_epoch}
    except Exception as e:
        db.rollback()
        logger.error(f"Error banning user: {e}")
        raise HTTPException(status_code=500, detail="Failed to ban user")


@router.delete("/{group_id}/ban/{user_id}")
async def unban_user(
    group_id: str,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Unban user. Owner/admin only."""
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")
    
    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid group ID format")
    
    # Check permissions
    check_group_role(db, group_uuid, current_user.account_id, ['owner', 'admin'])
    
    # Remove ban
    ban = db.query(GroupBan).filter(
        GroupBan.group_id == group_uuid,
        GroupBan.user_id == user_id
    ).first()
    
    if ban:
        db.delete(ban)
    
    # Update participant
    participant = db.query(GroupParticipant).filter(
        GroupParticipant.group_id == group_uuid,
        GroupParticipant.user_id == user_id
    ).first()
    
    if participant:
        participant.is_banned = False
    
    try:
        db.commit()
        return {"message": "User unbanned"}
    except Exception as e:
        db.rollback()
        logger.error(f"Error unbanning user: {e}")
        raise HTTPException(status_code=500, detail="Failed to unban user")


@router.post("/{group_id}/mute")
async def mute_group(
    group_id: str,
    request: MuteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Mute group notifications. None = unmute."""
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")
    
    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid group ID format")
    
    participant = db.query(GroupParticipant).filter(
        GroupParticipant.group_id == group_uuid,
        GroupParticipant.user_id == current_user.account_id
    ).first()
    
    if not participant:
        raise HTTPException(status_code=403, detail="Not a member")
    
    participant.mute_until = request.mute_until
    
    try:
        db.commit()
        return {"message": "Group muted" if request.mute_until else "Group unmuted"}
    except Exception as e:
        db.rollback()
        logger.error(f"Error muting group: {e}")
        raise HTTPException(status_code=500, detail="Failed to mute group")

