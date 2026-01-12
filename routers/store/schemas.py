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


# --- Cosmetics ---


class CosmeticBase(BaseModel):
    name: str
    description: Optional[str] = None
    price_gems: Optional[int] = None
    price_minor: Optional[int] = None
    is_premium: bool = False
    bucket: Optional[str] = None
    object_key: Optional[str] = None
    mime_type: Optional[str] = None


class AvatarResponse(CosmeticBase):
    id: str
    created_at: datetime
    price_usd: Optional[float] = None
    url: Optional[str] = None

    class Config:
        from_attributes = True


class FrameResponse(CosmeticBase):
    id: str
    created_at: datetime
    price_usd: Optional[float] = None
    url: Optional[str] = None

    class Config:
        from_attributes = True


class UserCosmeticResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    is_premium: bool
    purchase_date: datetime
    url: Optional[str] = None
    mime_type: Optional[str] = None

    class Config:
        from_attributes = True


class CosmeticPurchaseResponse(BaseModel):
    status: str
    message: str
    item_id: str
    purchase_date: datetime
    gems_spent: Optional[int] = None
    usd_spent: Optional[float] = None


class CosmeticSelectResponse(BaseModel):
    status: str
    message: str
    selected_id: str
