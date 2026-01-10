"""Store/Cosmetics schemas."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PurchaseResponse(BaseModel):
    success: bool
    remaining_gems: Optional[int] = None
    remaining_balance: Optional[float] = None
    message: str


class BuyGemsRequest(BaseModel):
    package_id: int = Field(..., description="ID of the gem package to purchase")

    class Config:
        json_schema_extra = {"example": {"package_id": 1}}


class GemPackageResponse(BaseModel):
    id: int
    price_usd: float
    gems_amount: int
    is_one_time: bool
    description: Optional[str]
    url: Optional[str] = None
    mime_type: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
