from fastapi import APIRouter, Depends, HTTPException, status, Body, Path, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import desc
import uuid
import os
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

from db import get_db
from models import Badge, User
from routers.dependencies import get_current_user

router = APIRouter(prefix="/badges", tags=["Badges"])

# ======== Helper Functions ========

def is_admin(current_user: dict, db: Session) -> bool:
    """
    Check if the current user is an admin based on their email matching ADMIN_EMAIL in env
    
    Args:
        current_user (dict): The current user's JWT claims
        db (Session): Database session
        
    Returns:
        bool: Whether the user is an admin
    """
    # Get admin email from environment or use default
    admin_email = os.getenv("ADMIN_EMAIL", "triviapay3@gmail.com")
    
    # Admin check is based on email
    email = current_user.get('email')
    if email and email.lower() == admin_email.lower():
        return True
        
    # Check in database
    if email:
        user = db.query(User).filter(User.email == email).first()
        if user and user.email.lower() == admin_email.lower():
            return True
            
    return False
    
def verify_admin(current_user: dict, db: Session) -> None:
    """
    Verify the user is an admin or raise an HTTP exception
    
    Args:
        current_user (dict): The current user's JWT claims
        db (Session): Database session
        
    Raises:
        HTTPException: If the user is not an admin
    """
    if not is_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required for this endpoint"
        )

# ======== Pydantic Models for Request/Response Validation ========

class BadgeBase(BaseModel):
    """Base model for Badge data"""
    name: str
    description: Optional[str] = None
    image_url: str
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
    current_user: dict = Depends(get_current_user),
    skip: int = 0,
    limit: int = 100
):
    """
    Get all available badges, ordered by level
    """
    badges = db.query(Badge).order_by(Badge.level).offset(skip).limit(limit).all()
    return badges

@router.get("/{badge_id}", response_model=BadgeResponse)
async def get_badge(
    badge_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
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
    current_user: dict = Depends(get_current_user)
):
    """
    Admin endpoint to create a new badge
    """
    # Check admin access
    verify_admin(current_user, db)
    
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
    
    return new_badge

@router.put("/admin/{badge_id}", response_model=BadgeResponse)
async def update_badge(
    badge_id: str = Path(..., description="The ID of the badge to update"),
    badge_update: BadgeUpdate = Body(..., description="Updated badge data"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Admin endpoint to update an existing badge
    """
    # Check admin access
    verify_admin(current_user, db)
    
    # Find the badge
    badge = db.query(Badge).filter(Badge.id == badge_id).first()
    if not badge:
        raise HTTPException(status_code=404, detail=f"Badge with ID {badge_id} not found")
    
    # Update badge fields
    badge.name = badge_update.name
    badge.description = badge_update.description
    badge.image_url = badge_update.image_url
    badge.level = badge_update.level
    
    # Now update all users who have this badge to use the new image URL
    db.query(User).filter(User.badge_id == badge_id).update(
        {"badge_image_url": badge_update.image_url},
        synchronize_session=False
    )
    
    db.commit()
    db.refresh(badge)
    
    return badge

# Endpoint to get badge assignments
@router.get("/admin/assignments", response_model=Dict[str, Any])
async def get_badge_assignments(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Admin endpoint to get badge assignment statistics
    """
    # Check admin access
    verify_admin(current_user, db)
    
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