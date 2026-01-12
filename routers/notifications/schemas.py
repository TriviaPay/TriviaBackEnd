"""Notifications domain schemas."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class RegisterPlayerRequest(BaseModel):
    player_id: str = Field(..., description="OneSignal player ID")
    platform: str = Field(..., description="Platform: 'ios', 'android', or 'web'")


class OneSignalPlayerResponse(BaseModel):
    player_id: str
    platform: str
    is_valid: bool
    created_at: datetime
    last_active: datetime
    last_failure_at: Optional[datetime] = None


class ListPlayersResponse(BaseModel):
    total: int
    limit: int
    offset: int
    players: List[OneSignalPlayerResponse]


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


class CreateTestNotificationRequest(BaseModel):
    title: str = Field(..., description="Notification title")
    body: str = Field(..., description="Notification body")
    notification_type: str = Field(default="test", description="Notification type")
    data: Optional[dict] = None
