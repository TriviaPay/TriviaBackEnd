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
