from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import datetime, timedelta
from typing import Optional, List
import uuid
import logging

from db import get_db
from models import User, Group, GroupParticipant
from routers.dependencies import get_current_user
from config import GROUPS_ENABLED, GROUP_MAX_PARTICIPANTS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/groups", tags=["Groups"])


class CreateGroupRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=100, example="My Test Group")
    about: Optional[str] = Field(None, max_length=500, example="This is a test group for E2EE messaging")
    photo_url: Optional[str] = Field(None, example="https://example.com/group-photo.jpg")
    
    class Config:
        json_schema_extra = {
            "example": {
                "title": "My Test Group",
                "about": "This is a test group for E2EE messaging",
                "photo_url": "https://example.com/group-photo.jpg"
            }
        }


class UpdateGroupRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=100, example="Updated Group Title")
    about: Optional[str] = Field(None, max_length=500, example="Updated group description")
    photo_url: Optional[str] = Field(None, example="https://example.com/new-group-photo.jpg")
    
    class Config:
        json_schema_extra = {
            "example": {
                "title": "Updated Group Title",
                "about": "Updated group description",
                "photo_url": "https://example.com/new-group-photo.jpg"
            }
        }


@router.post("")
def create_group(
    request: CreateGroupRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new group. Creator becomes owner.
    """
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")
    
    # Create group
    new_group = Group(
        id=uuid.uuid4(),
        title=request.title,
        about=request.about,
        photo_url=request.photo_url,
        created_by=current_user.account_id,
        max_participants=GROUP_MAX_PARTICIPANTS,
        group_epoch=0,
        is_closed=False
    )
    db.add(new_group)
    
    # Add creator as owner
    owner_participant = GroupParticipant(
        group_id=new_group.id,
        user_id=current_user.account_id,
        role='owner',
        joined_at=datetime.utcnow()
    )
    db.add(owner_participant)
    
    try:
        db.commit()
        db.refresh(new_group)
        
        return {
            "id": str(new_group.id),
            "title": new_group.title,
            "about": new_group.about,
            "photo_url": new_group.photo_url,
            "created_by": new_group.created_by,
            "created_at": new_group.created_at.isoformat(),
            "max_participants": new_group.max_participants,
            "group_epoch": new_group.group_epoch,
            "is_closed": new_group.is_closed
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating group: {e}")
        raise HTTPException(status_code=500, detail="Failed to create group")


@router.get("")
def list_groups(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List groups the user is a member of, ordered by last message time.
    """
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")
    
    participant_counts_subq = db.query(
        GroupParticipant.group_id.label("group_id"),
        func.count(GroupParticipant.user_id).label("participant_count")
    ).filter(
        GroupParticipant.is_banned.is_(False)
    ).group_by(GroupParticipant.group_id).subquery()

    member_subq = db.query(
        GroupParticipant.group_id.label("group_id"),
        GroupParticipant.role.label("role"),
        GroupParticipant.is_banned.label("is_banned")
    ).filter(
        GroupParticipant.user_id == current_user.account_id
    ).subquery()

    groups = db.query(
        Group,
        member_subq.c.role.label("my_role"),
        participant_counts_subq.c.participant_count.label("participant_count")
    ).join(
        member_subq, Group.id == member_subq.c.group_id
    ).outerjoin(
        participant_counts_subq, Group.id == participant_counts_subq.c.group_id
    ).filter(
        member_subq.c.is_banned.is_(False)
    ).order_by(
        desc(Group.updated_at)
    ).offset(offset).limit(limit).all()

    result = []
    for group, my_role, participant_count in groups:
        result.append({
            "id": str(group.id),
            "title": group.title,
            "about": group.about,
            "photo_url": group.photo_url,
            "created_at": group.created_at.isoformat(),
            "updated_at": group.updated_at.isoformat() if group.updated_at else None,
            "participant_count": participant_count or 0,
            "max_participants": group.max_participants,
            "group_epoch": group.group_epoch,
            "is_closed": group.is_closed,
            "my_role": my_role
        })
    
    return {"groups": result, "total": len(result)}


@router.get("/{group_id}")
def get_group(
    group_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get group details including participant count and user's role.
    """
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")
    
    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid group ID format")
    
    participant_counts_subq = db.query(
        GroupParticipant.group_id.label("group_id"),
        func.count(GroupParticipant.user_id).label("participant_count")
    ).filter(
        GroupParticipant.is_banned.is_(False)
    ).group_by(GroupParticipant.group_id).subquery()

    member_subq = db.query(
        GroupParticipant.group_id.label("group_id"),
        GroupParticipant.role.label("role"),
        GroupParticipant.is_banned.label("is_banned")
    ).filter(
        GroupParticipant.user_id == current_user.account_id,
        GroupParticipant.group_id == group_uuid
    ).subquery()

    row = db.query(
        Group,
        member_subq.c.role.label("my_role"),
        member_subq.c.is_banned.label("is_banned"),
        participant_counts_subq.c.participant_count.label("participant_count")
    ).outerjoin(
        member_subq, Group.id == member_subq.c.group_id
    ).outerjoin(
        participant_counts_subq, Group.id == participant_counts_subq.c.group_id
    ).filter(
        Group.id == group_uuid
    ).first()

    if not row:
        raise HTTPException(status_code=404, detail="Group not found")

    group, my_role, is_banned, participant_count = row

    if not my_role or is_banned:
        raise HTTPException(status_code=403, detail="Not a member of this group")
    
    return {
        "id": str(group.id),
        "title": group.title,
        "about": group.about,
        "photo_url": group.photo_url,
        "created_by": group.created_by,
        "created_at": group.created_at.isoformat(),
        "updated_at": group.updated_at.isoformat() if group.updated_at else None,
        "participant_count": participant_count or 0,
        "max_participants": group.max_participants,
        "group_epoch": group.group_epoch,
        "is_closed": group.is_closed,
        "my_role": my_role
    }


@router.patch("/{group_id}")
def update_group(
    group_id: str,
    request: UpdateGroupRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update group title, about, or photo. Owner/admin only.
    """
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")
    
    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid group ID format")
    
    group = db.query(Group).filter(Group.id == group_uuid).with_for_update().first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    if group.is_closed:
        raise HTTPException(status_code=409, detail="Group is closed")
    
    # Check if user is owner or admin
    participant = db.query(GroupParticipant).filter(
        GroupParticipant.group_id == group_uuid,
        GroupParticipant.user_id == current_user.account_id
    ).first()
    
    if not participant or participant.is_banned:
        raise HTTPException(status_code=403, detail="Not a member of this group")
    
    if participant.role not in ['owner', 'admin']:
        raise HTTPException(status_code=403, detail="Insufficient permissions. Owner or admin required.")
    
    # Update fields
    if request.title is not None:
        group.title = request.title
    if request.about is not None:
        group.about = request.about
    if request.photo_url is not None:
        group.photo_url = request.photo_url
    
    group.updated_at = datetime.utcnow()
    
    try:
        db.commit()
        db.refresh(group)
        
        return {
            "id": str(group.id),
            "title": group.title,
            "about": group.about,
            "photo_url": group.photo_url,
            "updated_at": group.updated_at.isoformat()
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating group: {e}")
        raise HTTPException(status_code=500, detail="Failed to update group")


@router.delete("/{group_id}")
def delete_group(
    group_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Close/archive a group. Owner only.
    """
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")
    
    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid group ID format")
    
    group = db.query(Group).filter(Group.id == group_uuid).with_for_update().first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    if group.is_closed:
        return {"message": "Group already closed"}
    
    # Check if user is owner
    participant = db.query(GroupParticipant).filter(
        GroupParticipant.group_id == group_uuid,
        GroupParticipant.user_id == current_user.account_id
    ).first()
    
    if not participant or participant.role != 'owner':
        raise HTTPException(status_code=403, detail="Only owner can close the group")
    
    # Soft delete (mark as closed)
    group.is_closed = True
    group.updated_at = datetime.utcnow()
    
    try:
        db.commit()
        return {"message": "Group closed successfully"}
    except Exception as e:
        db.rollback()
        logger.error(f"Error closing group: {e}")
        raise HTTPException(status_code=500, detail="Failed to close group")
