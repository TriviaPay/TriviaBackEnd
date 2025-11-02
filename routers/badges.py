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

def is_admin(user: User) -> bool:
    """
    Check if the current user is an admin based on database is_admin field
    
    Args:
        user (User): The current user object
        
    Returns:
        bool: Whether the user is an admin
    """
    return bool(user.is_admin)
    
def verify_admin(user: User) -> None:
    """
    Verify the user is an admin or raise an HTTP exception
    
    Args:
        user (User): The current user object
        
    Raises:
        HTTPException: If the user is not an admin
    """
    if not is_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required for this endpoint"
        )

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
        orm_mode = True

# ======== Badge Endpoints ========

@router.get("/", response_model=List[BadgeResponse])
async def get_all_badges(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    skip: int = 0,
    limit: int = 100
):
    """
    Get all available badges, ordered by level.
    
    Returns public image URLs directly (badges are stored with public S3 URLs,
    no presigning needed since they're shared assets).
    """
    badges = db.query(Badge).order_by(Badge.level).offset(skip).limit(limit).all()
    
    # Validate all badge URLs are public (log warnings if not)
    for badge in badges:
        if not validate_badge_url_is_public(badge.image_url):
            logger.warning(
                f"Badge {badge.id} ({badge.name}) has a non-public URL format. "
                f"Badges should use public S3 URLs for optimal performance."
            )
    
    return badges

@router.get("/{badge_id}", response_model=BadgeResponse)
async def get_badge(
    badge_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get a specific badge by ID
    """
    badge = db.query(Badge).filter(Badge.id == badge_id).first()
    if not badge:
        raise HTTPException(status_code=404, detail=f"Badge with ID {badge_id} not found")
    return badge

# ======== Admin Endpoints ========

@router.post("/admin", response_model=BadgeResponse)
async def create_badge(
    badge: BadgeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Admin endpoint to create a new badge.
    
    Note: image_url should be a public S3 URL (not presigned).
    Example: https://triviapay-assets.s3.us-east-2.amazonaws.com/badges/bronze.png
    """
    # Check admin access
    verify_admin(current_user)
    
    # Validate that the URL is public (warn if not, but allow)
    if not validate_badge_url_is_public(badge.image_url):
        logger.warning(
            f"Creating badge with URL that appears non-public: {badge.image_url[:100]}. "
            f"Badges should use public S3 URLs for optimal performance."
        )
    
    # Use provided ID or generate a new one
    badge_id = badge.id if badge.id else str(uuid.uuid4())
    
    # Check if a badge with this ID already exists
    if badge.id:
        existing = db.query(Badge).filter(Badge.id == badge_id).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Badge with ID {badge_id} already exists"
            )
    
    # Create a new badge
    new_badge = Badge(
        id=badge_id,
        name=badge.name,
        description=badge.description,
        image_url=badge.image_url,
        level=badge.level,
        created_at=datetime.utcnow()
    )
    
    db.add(new_badge)
    db.commit()
    db.refresh(new_badge)
    
    logger.info(f"Created badge {badge_id} ({badge.name}) with public URL: {badge.image_url[:80]}...")
    return new_badge

@router.put("/admin/{badge_id}", response_model=BadgeResponse)
async def update_badge(
    badge_id: str = Path(..., description="The ID of the badge to update"),
    badge_update: BadgeUpdate = Body(..., description="Updated badge data"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Admin endpoint to update an existing badge.
    
    Note: image_url should be a public S3 URL (not presigned).
    Updating a badge's image_url will also update all users who have this badge.
    """
    # Check admin access
    verify_admin(current_user)
    
    # Find the badge
    badge = db.query(Badge).filter(Badge.id == badge_id).first()
    if not badge:
        raise HTTPException(status_code=404, detail=f"Badge with ID {badge_id} not found")
    
    # Validate that the new URL is public (warn if not, but allow)
    if not validate_badge_url_is_public(badge_update.image_url):
        logger.warning(
            f"Updating badge {badge_id} with URL that appears non-public: {badge_update.image_url[:100]}. "
            f"Badges should use public S3 URLs for optimal performance."
        )
    
    # Update badge fields
    badge.name = badge_update.name
    badge.description = badge_update.description
    old_image_url = badge.image_url
    badge.image_url = badge_update.image_url
    badge.level = badge_update.level
    
    # Note: badge_image_url column has been removed from users table
    # Badge URLs are now retrieved directly from badges table using badge_id
    # No need to update users table when badge URL changes
    # Count how many users have this badge (for informational purposes)
    users_updated = db.query(User).filter(User.badge_id == badge_id).count()
    
    db.commit()
    db.refresh(badge)
    
    logger.info(
        f"Updated badge {badge_id} ({badge.name}). "
        f"Image URL changed, {users_updated} users updated with new badge image URL."
    )
    
    return badge

# Endpoint to get badge assignments
@router.get("/admin/assignments", response_model=Dict[str, Any])
async def get_badge_assignments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Admin endpoint to get badge assignment statistics
    """
    # Check admin access
    verify_admin(current_user)
    
    # Get counts of users per badge
    result = {}
    badges = db.query(Badge).all()
    
    for badge in badges:
        count = db.query(User).filter(User.badge_id == badge.id).count()
        result[badge.id] = {
            "badge_name": badge.name,
            "user_count": count
        }
    
    # Also get count of users with no badge
    no_badge_count = db.query(User).filter(User.badge_id == None).count()
    result["no_badge"] = {
        "badge_name": "No Badge",
        "user_count": no_badge_count
    }
    
    return {
        "assignments": result,
        "total_users": db.query(User).count()
    } 