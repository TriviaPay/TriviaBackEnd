from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field

from db import get_db
from models import User, Notification
from routers.dependencies import get_current_user
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["Notifications"])


# ======== Models ========

class NotificationResponse(BaseModel):
    id: int
    title: str
    body: str
    type: str
    data: Optional[dict] = None
    read: bool
    read_at: Optional[str] = None
    created_at: str
    
    class Config:
        from_attributes = True


class NotificationListResponse(BaseModel):
    notifications: List[NotificationResponse]
    total: int
    unread_count: int


class MarkReadRequest(BaseModel):
    notification_ids: List[int] = Field(..., description="List of notification IDs to mark as read")


# ======== Endpoints ========

@router.get("", response_model=NotificationListResponse)
async def get_notifications(
    limit: int = Query(50, ge=1, le=100, description="Maximum number of notifications to return"),
    offset: int = Query(0, ge=0, description="Number of notifications to skip"),
    unread_only: bool = Query(False, description="If true, only return unread notifications"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all notifications for the current user.
    
    Supports pagination and filtering by read status.
    """
    query = db.query(Notification).filter(
        Notification.user_id == current_user.account_id
    )
    
    if unread_only:
        query = query.filter(Notification.read == False)
    
    # Get total count before pagination
    total = query.count()
    
    # Get unread count
    unread_count = db.query(func.count(Notification.id)).filter(
        Notification.user_id == current_user.account_id,
        Notification.read == False
    ).scalar() or 0
    
    # Apply pagination and ordering
    notifications = query.order_by(desc(Notification.created_at)).offset(offset).limit(limit).all()
    
    return NotificationListResponse(
        notifications=[
            NotificationResponse(
                id=n.id,
                title=n.title,
                body=n.body,
                type=n.type,
                data=n.data,
                read=n.read,
                read_at=n.read_at.isoformat() if n.read_at else None,
                created_at=n.created_at.isoformat()
            )
            for n in notifications
        ],
        total=total,
        unread_count=unread_count
    )


@router.get("/unread-count")
async def get_unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get the count of unread notifications for the current user.
    """
    unread_count = db.query(func.count(Notification.id)).filter(
        Notification.user_id == current_user.account_id,
        Notification.read == False
    ).scalar() or 0
    
    return {
        "unread_count": unread_count
    }


@router.put("/mark-read", response_model=dict)
async def mark_notifications_read(
    request: MarkReadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Mark one or more notifications as read.
    """
    if not request.notification_ids:
        raise HTTPException(status_code=400, detail="notification_ids cannot be empty")
    
    # Verify all notifications belong to the current user
    notifications = db.query(Notification).filter(
        Notification.id.in_(request.notification_ids),
        Notification.user_id == current_user.account_id
    ).all()
    
    if len(notifications) != len(request.notification_ids):
        raise HTTPException(
            status_code=404,
            detail="One or more notifications not found or not owned by user"
        )
    
    # Mark as read
    now = datetime.utcnow()
    for notification in notifications:
        if not notification.read:
            notification.read = True
            notification.read_at = now
    
    db.commit()
    
    return {
        "message": f"Marked {len(notifications)} notification(s) as read",
        "marked_count": len(notifications)
    }


@router.put("/mark-all-read", response_model=dict)
async def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Mark all unread notifications as read for the current user.
    """
    updated_count = db.query(Notification).filter(
        Notification.user_id == current_user.account_id,
        Notification.read == False
    ).update({
        Notification.read: True,
        Notification.read_at: datetime.utcnow()
    })
    
    db.commit()
    
    return {
        "message": f"Marked {updated_count} notification(s) as read",
        "marked_count": updated_count
    }


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Delete a specific notification.
    """
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == current_user.account_id
    ).first()
    
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    db.delete(notification)
    db.commit()
    
    return {
        "message": "Notification deleted",
        "notification_id": notification_id
    }


@router.delete("")
async def delete_all_notifications(
    read_only: bool = Query(False, description="If true, only delete read notifications"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Delete all notifications for the current user.
    Optionally delete only read notifications.
    """
    query = db.query(Notification).filter(
        Notification.user_id == current_user.account_id
    )
    
    if read_only:
        query = query.filter(Notification.read == True)
    
    deleted_count = query.count()
    query.delete(synchronize_session=False)
    db.commit()
    
    return {
        "message": f"Deleted {deleted_count} notification(s)",
        "deleted_count": deleted_count
    }

