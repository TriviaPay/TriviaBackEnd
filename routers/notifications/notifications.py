import logging
import os
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, desc, func, or_
from sqlalchemy.orm import Session

from db import get_db
from models import Notification, User
from routers.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["Notifications"])
NOTIFICATIONS_DEBUG = os.getenv("NOTIFICATIONS_DEBUG", "false").lower() == "true"


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
    notification_ids: List[int] = Field(
        ..., description="List of notification IDs to mark as read"
    )


# ======== Endpoints ========


@router.get("", response_model=NotificationListResponse)
async def get_notifications(
    limit: int = Query(
        50, ge=1, le=100, description="Maximum number of notifications to return"
    ),
    offset: int = Query(0, ge=0, description="Number of notifications to skip"),
    unread_only: bool = Query(
        False, description="If true, only return unread notifications"
    ),
    cursor: Optional[str] = Query(
        None, description="Cursor for keyset pagination: ISO8601|id"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get all notifications for the current user.

    Supports pagination and filtering by read status.
    """
    if NOTIFICATIONS_DEBUG:
        logger.info(
            f"🔍 Querying notifications for account_id={current_user.account_id} (descope_user_id={current_user.descope_user_id}, type: {type(current_user.account_id)})"
        )

    base_query = db.query(Notification).filter(
        Notification.user_id == current_user.account_id
    )

    if unread_only:
        base_query = base_query.filter(Notification.read == False)

    total_expr = func.count(Notification.id)
    if unread_only:
        total_expr = func.sum(case((Notification.read == False, 1), else_=0))

    counts = (
        db.query(total_expr, func.sum(case((Notification.read == False, 1), else_=0)))
        .filter(Notification.user_id == current_user.account_id)
        .first()
    )
    total = counts[0] if counts and counts[0] is not None else 0
    unread_count = counts[1] if counts and counts[1] is not None else 0

    query = base_query
    if cursor:
        try:
            cursor_parts = cursor.split("|")
            cursor_time = datetime.fromisoformat(cursor_parts[0])
            cursor_id = int(cursor_parts[1]) if len(cursor_parts) > 1 else None
            if cursor_id is not None:
                query = query.filter(
                    or_(
                        Notification.created_at < cursor_time,
                        and_(
                            Notification.created_at == cursor_time,
                            Notification.id < cursor_id,
                        ),
                    )
                )
            else:
                query = query.filter(Notification.created_at < cursor_time)
        except Exception:
            pass

    # Apply pagination and ordering
    if cursor:
        notifications = (
            query.order_by(desc(Notification.created_at), desc(Notification.id))
            .limit(limit)
            .all()
        )
    else:
        notifications = (
            query.order_by(desc(Notification.created_at), desc(Notification.id))
            .offset(offset)
            .limit(limit)
            .all()
        )

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
                created_at=n.created_at.isoformat(),
            )
            for n in notifications
        ],
        total=total,
        unread_count=unread_count,
    )


@router.get("/unread-count")
async def get_unread_count(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """
    Get the count of unread notifications for the current user.
    """
    unread_count = (
        db.query(func.count(Notification.id))
        .filter(
            Notification.user_id == current_user.account_id, Notification.read == False
        )
        .scalar()
        or 0
    )

    return {"unread_count": unread_count}


@router.put("/mark-read", response_model=dict)
async def mark_notifications_read(
    request: MarkReadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Mark one or more notifications as read.
    """
    if not request.notification_ids:
        raise HTTPException(status_code=400, detail="notification_ids cannot be empty")

    # Verify all notifications belong to the current user
    notifications_count = (
        db.query(func.count(Notification.id))
        .filter(
            Notification.id.in_(request.notification_ids),
            Notification.user_id == current_user.account_id,
        )
        .scalar()
        or 0
    )

    if notifications_count != len(request.notification_ids):
        raise HTTPException(
            status_code=404,
            detail="One or more notifications not found or not owned by user",
        )

    # Mark as read
    now = datetime.utcnow()
    updated_count = (
        db.query(Notification)
        .filter(
            Notification.id.in_(request.notification_ids),
            Notification.user_id == current_user.account_id,
            Notification.read == False,
        )
        .update(
            {Notification.read: True, Notification.read_at: now},
            synchronize_session=False,
        )
    )
    db.commit()

    return {
        "message": f"Marked {updated_count} notification(s) as read",
        "marked_count": updated_count,
    }


@router.put("/mark-all-read", response_model=dict)
async def mark_all_notifications_read(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """
    Mark all unread notifications as read for the current user.
    """
    updated_count = (
        db.query(Notification)
        .filter(
            Notification.user_id == current_user.account_id, Notification.read == False
        )
        .update({Notification.read: True, Notification.read_at: datetime.utcnow()})
    )

    db.commit()

    return {
        "message": f"Marked {updated_count} notification(s) as read",
        "marked_count": updated_count,
    }


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a specific notification.
    """
    notification = (
        db.query(Notification)
        .filter(
            Notification.id == notification_id,
            Notification.user_id == current_user.account_id,
        )
        .first()
    )

    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    db.delete(notification)
    db.commit()

    return {"message": "Notification deleted", "notification_id": notification_id}


@router.delete("")
async def delete_all_notifications(
    read_only: bool = Query(
        False, description="If true, only delete read notifications"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
        "deleted_count": deleted_count,
    }


# ======== Test/Development Endpoints ========


class CreateTestNotificationRequest(BaseModel):
    title: str = Field(..., description="Notification title")
    body: str = Field(..., description="Notification body")
    notification_type: str = Field(default="test", description="Notification type")
    data: Optional[dict] = None


@router.post("/test", response_model=NotificationResponse)
async def create_test_notification(
    request: CreateTestNotificationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a test notification for the current user.
    Useful for testing the notification system without needing OneSignal.
    """
    from utils.notification_storage import create_notification

    notification = create_notification(
        db=db,
        user_id=current_user.account_id,
        title=request.title,
        body=request.body,
        notification_type=request.notification_type,
        data=request.data,
    )

    logger.info(
        f"Created test notification {notification.id} for user {current_user.account_id}"
    )

    return NotificationResponse(
        id=notification.id,
        title=notification.title,
        body=notification.body,
        type=notification.type,
        data=notification.data,
        read=notification.read,
        read_at=notification.read_at.isoformat() if notification.read_at else None,
        created_at=notification.created_at.isoformat(),
    )
