"""Support schemas."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class FAQBase(BaseModel):
    question: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)


class FAQCreateRequest(FAQBase):
    pass


class FAQUpdateRequest(BaseModel):
    question: Optional[str] = Field(None, min_length=1)
    answer: Optional[str] = Field(None, min_length=1)


class FAQResponse(FAQBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class FAQListResponse(BaseModel):
    faqs: List[FAQResponse]


class FAQDeleteResponse(BaseModel):
    deleted: bool
    faq_id: int
