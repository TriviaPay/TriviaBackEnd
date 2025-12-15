from fastapi import APIRouter, Depends, HTTPException, status, Body, Path, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import desc
import uuid
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

from db import get_db
from models import Badge, User
from routers.dependencies import get_current_user

router = APIRouter(prefix="/badges", tags=["Badges"])

logger = logging.getLogger(__name__)

# ======== Helper Functions ========

def validate_badge_url_is_public(image_url: str) -> bool:
    """
    Validate that badge image_url is a public S3 URL (not a presigned URL).
    
    Badges should use public URLs since they're shared assets that all users can access.
    Presigned URLs are not needed and would expire unnecessarily.
    
    Args:
        image_url: The image URL to validate
        
    Returns:
        bool: True if URL appears to be a public URL (doesn't contain presigned query params)
    """
    # Presigned URLs contain query parameters like ?X-Amz-Algorithm=...
    # Public URLs should be clean S3 URLs or CDN URLs
    if not image_url:
        return False
    
    # Check if it looks like a presigned URL (has AWS signature params)
    presigned_indicators = ['X-Amz-Algorithm', 'X-Amz-Credential', 'X-Amz-Signature', 'X-Amz-Date']
    if any(indicator in image_url for indicator in presigned_indicators):
        logger.warning(f"Badge URL appears to be presigned (should be public): {image_url[:100]}...")
        return False
    
    # Check if it's an S3 URL or CDN URL
    public_url_patterns = ['s3.amazonaws.com', 's3.', 'amazonaws.com', 'cdn.', '.com/', '.org/']
    if any(pattern in image_url for pattern in public_url_patterns):
        return True
    
    # Allow other public URL formats (CDN, etc.)
    if image_url.startswith('http://') or image_url.startswith('https://'):
        return True
    
    return False

# ======== Pydantic Models for Request/Response Validation ========

class BadgeBase(BaseModel):
    """
    Base model for Badge data.
    
    Note: image_url should be a public S3 URL or CDN URL, not a presigned URL.
    Badges are shared assets (only 4 total), so they should be publicly accessible
    to avoid unnecessary presigned URL generation and expiration.
    """
    name: str
    description: Optional[str] = None
    image_url: str  # Should be a public S3 URL (e.g., https://bucket.s3.region.amazonaws.com/badges/badge.png)
    level: int

class BadgeCreate(BadgeBase):
    """Schema for creating a new badge"""
    id: Optional[str] = None  # Allow custom ID or generate one
    
class BadgeUpdate(BadgeBase):
    """Schema for updating a badge"""
    pass

class BadgeResponse(BadgeBase):
    """Schema for badge response"""
    id: str
    created_at: datetime
    
    class Config:
        from_attributes = True

# Admin endpoints moved to admin.py router 