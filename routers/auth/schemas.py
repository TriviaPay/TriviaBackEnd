"""Domain schemas."""

from datetime import date as DateType
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class BindPasswordData(BaseModel):
    email: str = Field(..., description="User email (loginId)")
    password: str = Field(..., description="New password to bind")
    username: str = Field(..., description="Display name / username to set")
    country: str = Field(..., description="User country")
    date_of_birth: DateType = Field(..., description="User date of birth (YYYY-MM-DD)")
    referral_code: Optional[str] = Field(None, description="Optional referral code")
    device_uuid: Optional[str] = Field(
        None, description="Unique device identifier from Descope signup/signin"
    )
    app_version: Optional[str] = Field(
        None, description="App version reported at signup/signin"
    )
    os: Optional[str] = Field(None, description="Device OS reported at signup/signin")
    device_name: Optional[str] = Field(
        None, description="Device name reported at signup/signin"
    )


class DevSignInRequest(BaseModel):
    email: str = Field(
        ..., description="Email address (loginId)", example="triviapay3@gmail.com"
    )
    password: str = Field(..., description="User password", example="Trivia@1")


class ReferralCheck(BaseModel):
    referral_code: str = Field(..., description="Referral code to validate")


class ExtendedProfileUpdate(BaseModel):
    """
    Model for updating extended user profile data including name, address, and contact information.
    Username is not included here as it can only be updated once per user and requires a purchase after that.
    """

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    mobile: Optional[str] = None
    country_code: Optional[str] = None
    gender: Optional[str] = None
    street_1: Optional[str] = None
    street_2: Optional[str] = None
    suite_or_apt_number: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    country: Optional[str] = None


# ======== Admin Schemas ========


class DrawConfigUpdateRequest(BaseModel):
    is_custom: Optional[bool] = Field(
        None, description="Whether to use custom winner count"
    )
    custom_winner_count: Optional[int] = Field(
        None, description="Custom number of winners when is_custom is True"
    )
    draw_time_hour: Optional[int] = Field(
        None, ge=0, le=23, description="Hour of the day for the draw (0-23)"
    )
    draw_time_minute: Optional[int] = Field(
        None, ge=0, le=59, description="Minute of the hour for the draw (0-59)"
    )
    draw_timezone: Optional[str] = Field(
        None, description="Timezone for the draw (e.g., US/Eastern)"
    )


class DrawConfigResponse(BaseModel):
    is_custom: bool
    custom_winner_count: Optional[int] = None
    draw_time_hour: int
    draw_time_minute: int
    draw_timezone: str


class DrawResponse(BaseModel):
    status: str
    draw_date: DateType
    total_participants: int
    total_winners: int
    prize_pool: float
    winners: List[Dict[str, Any]]


class UserAdminStatus(BaseModel):
    account_id: int
    email: str
    username: Optional[str] = None
    is_admin: bool

    class Config:
        from_attributes = True


class UpdateAdminStatusRequest(BaseModel):
    is_admin: bool = Field(..., description="Admin status to set for the user")


class AdminStatusResponse(BaseModel):
    account_id: int
    email: str
    username: Optional[str] = None
    is_admin: bool
    message: str


class AppVersionResponse(BaseModel):
    user_id: int
    device_uuid: str
    device_name: Optional[str] = None
    app_version: str
    os: str
    reported_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class GemPackageRequest(BaseModel):
    price_minor: int = Field(..., description="Price in minor units (cents)")
    gems_amount: int = Field(..., description="Number of gems in the package")
    is_one_time: bool = Field(False, description="Whether this is a one-time offer")
    description: Optional[str] = Field(None, description="Description of the package")
    bucket: Optional[str] = Field(
        None, description="S3 bucket name for the package image"
    )
    object_key: Optional[str] = Field(
        None, description="S3 object key for the package image"
    )
    mime_type: Optional[str] = Field(None, description="MIME type of the image")


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


class BadgeBase(BaseModel):
    name: str
    description: Optional[str] = None
    image_url: str
    level: int


class BadgeCreate(BadgeBase):
    id: Optional[str] = None


class BadgeUpdate(BadgeBase):
    pass


class BadgeResponse(BadgeBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True


class CosmeticBase(BaseModel):
    name: str
    description: Optional[str] = None
    price_gems: Optional[int] = None
    price_minor: Optional[int] = None
    is_premium: bool = False
    bucket: Optional[str] = None
    object_key: Optional[str] = None
    mime_type: Optional[str] = None


class AvatarCreate(CosmeticBase):
    id: Optional[str] = None


class AvatarResponse(CosmeticBase):
    id: str
    created_at: datetime
    url: Optional[str] = None
    mime_type: Optional[str] = None

    class Config:
        from_attributes = True


class FrameCreate(CosmeticBase):
    id: Optional[str] = None


class FrameResponse(CosmeticBase):
    id: str
    created_at: datetime
    url: Optional[str] = None
    mime_type: Optional[str] = None

    class Config:
        from_attributes = True


class BulkImportResponse(BaseModel):
    status: str
    message: str
    imported_count: int
    errors: List[str] = []


class CreateSubscriptionPlanRequest(BaseModel):
    name: str = "$5 Monthly Subscription"
    description: Optional[str] = "$5 monthly subscription for trivia bronze mode access"
    price_usd: float = 5.0
    unit_amount_minor: Optional[int] = 500
    currency: str = "usd"
    interval: str = "month"
    interval_count: int = 1
    billing_interval: Optional[str] = None
    stripe_price_id: Optional[str] = None
    livemode: bool = False


class SubscriptionPlanResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    price_usd: float
    unit_amount_minor: Optional[int] = None
    currency: Optional[str] = None
    interval: Optional[str] = None
    interval_count: int
    billing_interval: Optional[str] = None
    stripe_price_id: Optional[str] = None
    livemode: bool
    trial_period_days: Optional[int] = None
    tax_behavior: Optional[str] = None
    features: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CreateSubscriptionRequest(BaseModel):
    user_id: Optional[int] = Field(
        None,
        description="User account ID to create subscription for. If not provided, creates for current user.",
    )
    plan_id: int = Field(..., description="Subscription plan ID (required).")
