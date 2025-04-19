from fastapi import APIRouter, Depends, HTTPException, status, Body, Path, Query, Request
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
from routers.dependencies import get_current_user, get_admin_user

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

@router.post("/admin/badges", response_model=BadgeResponse)
async def create_badge(
    request: Request,
    badge: BadgeCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Create a new badge
    
    This endpoint requires admin privileges
    """
    logger.info(f"Admin creating new badge: {badge.name}")
    
    try:
        # Check if badge with same name exists
        existing_badge = db.query(Badge).filter(Badge.name == badge.name).first()
        if existing_badge:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Badge with name '{badge.name}' already exists"
            )
            
        # Create new badge
        new_badge = Badge(
            name=badge.name,
            description=badge.description,
            image_url=badge.image_url,
            requirement_type=badge.requirement_type,
            requirement_value=badge.requirement_value,
            rarity=badge.rarity
        )
        
        db.add(new_badge)
        db.commit()
        db.refresh(new_badge)
        
        return BadgeResponse.from_orm(new_badge)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating badge: {str(e)}", exc_info=True)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating badge: {str(e)}"
        )

@router.put("/admin/{badge_id}", response_model=BadgeResponse)
async def update_badge(
    request: Request,
    badge_id: str = Path(..., description="The ID of the badge to update"),
    badge_update: BadgeUpdate = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Update an existing badge
    
    This endpoint requires admin privileges
    """
    logger.info(f"Admin updating badge {badge_id}: {badge_update.dict(exclude_unset=True)}")
    
    try:
        # Get the badge
        badge = db.query(Badge).filter(Badge.id == badge_id).first()
        if not badge:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Badge with ID {badge_id} not found"
            )
        
        # Update badge fields
        update_data = badge_update.dict(exclude_unset=True)
        for key, value in update_data.items():
            setattr(badge, key, value)
        
        db.commit()
        db.refresh(badge)
        
        return BadgeResponse.from_orm(badge)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating badge {badge_id}: {str(e)}", exc_info=True)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating badge: {str(e)}"
        )

@router.delete("/admin/{badge_id}", response_model=dict)
async def delete_badge(
    request: Request,
    badge_id: str = Path(..., description="The ID of the badge to delete"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Delete a badge
    
    This endpoint requires admin privileges
    """
    logger.info(f"Admin deleting badge {badge_id}")
    
    try:
        # Get the badge
        badge = db.query(Badge).filter(Badge.id == badge_id).first()
        if not badge:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Badge with ID {badge_id} not found"
            )
        
        # Delete the badge
        db.delete(badge)
        db.commit()
        
        return {"message": f"Badge {badge_id} deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting badge {badge_id}: {str(e)}", exc_info=True)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting badge: {str(e)}"
        )

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