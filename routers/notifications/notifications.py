from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .schemas import (
    CreateTestNotificationRequest,
    MarkReadRequest,
    NotificationListResponse,
    NotificationResponse,
)
from .service import (
    create_test_notification as service_create_test_notification,
    delete_all_notifications as service_delete_all_notifications,
    delete_notification as service_delete_notification,
    get_notifications as service_get_notifications,
    get_unread_count as service_get_unread_count,
    mark_all_notifications_read as service_mark_all_notifications_read,
    mark_notifications_read as service_mark_notifications_read,
)

router = APIRouter(prefix="/notifications", tags=["Notifications"])


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
    current_user = Depends(get_current_user),
):
    """Get all notifications for the current user."""
    return service_get_notifications(
        db,
        current_user=current_user,
        limit=limit,
        offset=offset,
        unread_only=unread_only,
        cursor=cursor,
    )


@router.get("/unread-count")
async def get_unread_count(
    db: Session = Depends(get_db), current_user = Depends(get_current_user)
):
    """Get the count of unread notifications for the current user."""
    return service_get_unread_count(db, current_user=current_user)


@router.put("/mark-read", response_model=dict)
async def mark_notifications_read(
    request: MarkReadRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Mark one or more notifications as read."""
    return service_mark_notifications_read(db, current_user=current_user, request=request)


@router.put("/mark-all-read", response_model=dict)
async def mark_all_notifications_read(
    db: Session = Depends(get_db), current_user = Depends(get_current_user)
):
    """Mark all unread notifications as read for the current user."""
    return service_mark_all_notifications_read(db, current_user=current_user)


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Delete a specific notification."""
    return service_delete_notification(
        db, current_user=current_user, notification_id=notification_id
    )


@router.delete("")
async def delete_all_notifications(
    read_only: bool = Query(
        False, description="If true, only delete read notifications"
    ),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Delete all notifications for the current user."""
    return service_delete_all_notifications(
        db, current_user=current_user, read_only=read_only
    )


@router.post("/test", response_model=NotificationResponse)
async def create_test_notification(
    request: CreateTestNotificationRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Create a test notification for the current user."""
    return service_create_test_notification(
        db, current_user=current_user, request=request
    )
