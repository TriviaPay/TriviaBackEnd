from datetime import datetime

from pydantic import BaseModel, Field


class AppVersionUpsertRequest(BaseModel):
    os: str = Field(..., description="Platform/OS identifier (ios, android, web)")
    latest_version: str = Field(..., description="Latest app version string")


class AppVersionResponse(BaseModel):
    os: str
    latest_version: str
    created_at: datetime
    updated_at: datetime
