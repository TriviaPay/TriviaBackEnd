from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta
from typing import Optional
import uuid
import secrets
import logging

from db import get_db
from models import User, Group, GroupInvite, GroupParticipant, GroupBan
from routers.dependencies import get_current_user
from config import GROUPS_ENABLED, GROUP_MAX_PARTICIPANTS, GROUP_INVITE_EXPIRY_HOURS
from routers.group_members import check_group_role, increment_group_epoch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/groups", tags=["Group Invites"])


class CreateInviteRequest(BaseModel):
    type: str = Field(..., pattern="^(link|direct)$", example="link")
    expires_at: Optional[datetime] = Field(None, example="2025-11-12T16:00:00Z")
    max_uses: Optional[int] = Field(None, ge=1, example=10)
    target_user_id: Optional[int] = Field(None, example=1142961859)
    
    class Config:
        json_schema_extra = {
            "example": {
                "type": "link",
                "expires_at": "2025-11-12T16:00:00Z",
            "max_uses": 10,
            "target_user_id": 1142961859
        }
    }


class JoinGroupRequest(BaseModel):
    code: str = Field(..., example="ABC123XYZ")
    
    class Config:
        json_schema_extra = {
            "example": {
                "code": "ABC123XYZ"
            }
        }


def generate_invite_code() -> str:
    """Generate a short, URL-safe invite code."""
    return secrets.token_urlsafe(8)[:12].upper()


@router.post("/{group_id}/invites")
async def create_invite(
    group_id: str,
    request: CreateInviteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create invite link/code. Owner/admin only."""
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
    
    # Set default expiry
    expires_at = request.expires_at
    if not expires_at:
        expires_at = datetime.utcnow() + timedelta(hours=GROUP_INVITE_EXPIRY_HOURS)
    if expires_at and expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="EXPIRY_IN_PAST")

    if request.type == "direct" and not request.target_user_id:
        raise HTTPException(status_code=400, detail="TARGET_USER_REQUIRED")
    
    # Generate unique code (retry on unique constraint)
    for _ in range(5):
        code = generate_invite_code()
        invite = GroupInvite(
            id=uuid.uuid4(),
            group_id=group_uuid,
            created_by=current_user.account_id,
            type=request.type,
            code=code,
            expires_at=expires_at,
            max_uses=request.max_uses,
            uses=0,
            target_user_id=request.target_user_id
        )
        db.add(invite)
        try:
            db.commit()
            db.refresh(invite)
            return {
                "id": str(invite.id),
                "code": invite.code,
                "type": invite.type,
                "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
                "max_uses": invite.max_uses,
                "uses": invite.uses,
                "target_user_id": invite.target_user_id
            }
        except IntegrityError:
            db.rollback()
            continue
        except Exception as e:
            db.rollback()
            logger.error(f"Error creating invite: {e}")
            raise HTTPException(status_code=500, detail="Failed to create invite")
    
    raise HTTPException(status_code=500, detail="Failed to generate unique invite code")


@router.get("/{group_id}/invites")
async def list_invites(
    group_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List active invites. Owner/admin only."""
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")
    
    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid group ID format")
    
    # Check permissions
    check_group_role(db, group_uuid, current_user.account_id, ['owner', 'admin'])
    
    # Get active invites (not expired, not maxed out)
    now = datetime.utcnow()
    active_invites = db.query(GroupInvite).filter(
        GroupInvite.group_id == group_uuid,
        GroupInvite.expires_at > now,
        or_(GroupInvite.max_uses.is_(None), GroupInvite.uses < GroupInvite.max_uses)
    ).all()
    
    active_invites_payload = []
    for invite in active_invites:
        active_invites_payload.append({
            "id": str(invite.id),
            "code": invite.code,
            "type": invite.type,
            "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
            "max_uses": invite.max_uses,
            "uses": invite.uses,
            "created_at": invite.created_at.isoformat() if invite.created_at else None,
            "target_user_id": invite.target_user_id
        })
    
    return {"invites": active_invites_payload}


@router.delete("/{group_id}/invites/{invite_id}")
async def revoke_invite(
    group_id: str,
    invite_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Revoke invite. Owner/admin only."""
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")
    
    try:
        group_uuid = uuid.UUID(group_id)
        invite_uuid = uuid.UUID(invite_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")
    
    # Check permissions
    check_group_role(db, group_uuid, current_user.account_id, ['owner', 'admin'])
    
    invite = db.query(GroupInvite).filter(
        GroupInvite.id == invite_uuid,
        GroupInvite.group_id == group_uuid
    ).first()
    
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    
    db.delete(invite)
    
    try:
        db.commit()
        return {"message": "Invite revoked"}
    except Exception as e:
        db.rollback()
        logger.error(f"Error revoking invite: {e}")
        raise HTTPException(status_code=500, detail="Failed to revoke invite")


@router.post("/join")
async def join_group(
    request: JoinGroupRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Join group via invite code. Validates capacity, bans. Triggers rekey."""
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")
    
    # Find invite
    invite = db.query(GroupInvite).filter(
        GroupInvite.code == request.code
    ).with_for_update().first()
    if not invite:
        raise HTTPException(status_code=404, detail="Invalid invite code")
    
    # Check expiry
    if invite.expires_at and invite.expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="GONE")
    
    # Check max uses
    if invite.max_uses and invite.uses >= invite.max_uses:
        raise HTTPException(status_code=409, detail="MAX_USES")

    if invite.type == "direct" and invite.target_user_id != current_user.account_id:
        raise HTTPException(status_code=403, detail="NOT_INVITED")
    
    group = db.query(Group).filter(Group.id == invite.group_id).with_for_update().first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    if group.is_closed:
        raise HTTPException(status_code=403, detail="Group is closed")
    
    # Check if banned
    ban = db.query(GroupBan).filter(
        GroupBan.group_id == group.id,
        GroupBan.user_id == current_user.account_id
    ).first()
    
    if ban:
        raise HTTPException(status_code=403, detail="BANNED")
    
    # Check if already a member
    existing = db.query(GroupParticipant).filter(
        GroupParticipant.group_id == group.id,
        GroupParticipant.user_id == current_user.account_id
    ).first()
    
    if existing:
        if existing.is_banned:
            raise HTTPException(status_code=403, detail="BANNED")
        return {"message": "Already a member", "group_id": str(group.id)}
    
    # Check capacity
    participant_count = db.query(GroupParticipant).filter(
        GroupParticipant.group_id == group.id,
        GroupParticipant.is_banned == False
    ).count()
    
    if participant_count >= GROUP_MAX_PARTICIPANTS:
        raise HTTPException(status_code=409, detail="GROUP_FULL")
    
    # Add participant
    if not existing:
        participant = GroupParticipant(
            group_id=group.id,
            user_id=current_user.account_id,
            role='member',
            joined_at=datetime.utcnow()
        )
        db.add(participant)
    
    # Increment invite uses
    invite.uses += 1
    
    # Increment epoch (triggers rekey)
    increment_group_epoch(db, group)
    
    try:
        db.commit()
        return {
            "message": "Joined group",
            "group_id": str(group.id),
            "new_epoch": group.group_epoch
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Error joining group: {e}")
        raise HTTPException(status_code=500, detail="Failed to join group")
