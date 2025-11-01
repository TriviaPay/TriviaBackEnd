from fastapi import APIRouter, Depends, HTTPException, status, Request, Body, Path, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime, date
import random
import string
from db import get_db
from models import User, Badge, Avatar, Frame
from utils.storage import presign_get
from routers.dependencies import get_current_user
import logging
from utils import get_letter_profile_pic
from descope import DescopeClient
from config import DESCOPE_PROJECT_ID, DESCOPE_MANAGEMENT_KEY, DESCOPE_JWT_LEEWAY

router = APIRouter(prefix="/profile", tags=["Profile"])

client = DescopeClient(project_id=DESCOPE_PROJECT_ID, management_key=DESCOPE_MANAGEMENT_KEY, jwt_validation_leeway=DESCOPE_JWT_LEEWAY)

def generate_referral_code():
    """Generate a unique 5-digit referral code"""
    return ''.join(random.choices(string.digits, k=5))


# Badge related models
class BadgeAssignment(BaseModel):
    badge_id: str

@router.get("/badges", response_model=List[dict])
async def get_user_badges(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get the current badge assigned to the user and all available badges
    """
    try:
        # Get the current user
        user = db.query(User).filter(User.account_id == current_user.account_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found")
        
        # Get all badges
        badges = db.query(Badge).order_by(Badge.level).all()
        
        # Format response
        badges_list = []
        for badge in badges:
            badge_dict = {
                "id": badge.id,
                "name": badge.name,
                "description": badge.description,
                "image_url": badge.image_url,
                "level": badge.level,
                "is_current": user.badge_id == badge.id
            }
            badges_list.append(badge_dict)
        
        return badges_list
    
    except Exception as e:
        logging.error(f"Error getting user badges: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving badges: {str(e)}")

@router.post("/assign-badge", status_code=200)
async def assign_badge(
    badge_data: BadgeAssignment,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Assign a badge to the user
    """
    try:
        # Get the current user
        user = db.query(User).filter(User.account_id == current_user.account_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found")
        
        # Get the badge
        badge = db.query(Badge).filter(Badge.id == badge_data.badge_id).first()
        if not badge:
            return {
                "status": "error",
                "message": f"Badge with ID {badge_data.badge_id} not found",
                "code": "BADGE_NOT_FOUND"
            }
        
        # Update the user's badge
        user.badge_id = badge.id
        user.badge_image_url = badge.image_url
        
        # Commit changes
        db.commit()
        
        return {
            "status": "success",
            "message": f"Badge '{badge.name}' successfully assigned",
            "data": {
                "badge_id": badge.id,
                "badge_name": badge.name,
                "badge_image_url": badge.image_url
            }
        }
    
    except Exception as e:
        db.rollback()
        logging.error(f"Error assigning badge: {str(e)}")
        return {
            "status": "error",
            "message": f"Error assigning badge: {str(e)}",
            "code": "BADGE_ASSIGNMENT_ERROR"
        }

@router.post("/remove-badge", status_code=200)
async def remove_badge(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Remove the current badge from the user
    """
    try:
        # Get the current user
        user = db.query(User).filter(User.account_id == current_user.account_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found")
        
        # Check if user has a badge
        if not user.badge_id:
            return {
                "status": "error",
                "message": "User does not have a badge assigned",
                "code": "NO_BADGE_ASSIGNED"
            }
        
        # Remove the badge
        user.badge_id = None
        user.badge_image_url = None
        
        # Commit changes
        db.commit()
        
        return {
            "status": "success",
            "message": "Badge successfully removed"
        }
    
    except Exception as e:
        db.rollback()
        logging.error(f"Error removing badge: {str(e)}")
        return {
            "status": "error",
            "message": f"Error removing badge: {str(e)}",
            "code": "BADGE_REMOVAL_ERROR"
        } 

@router.get("/gems", status_code=200)
async def get_user_gems(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get the user's gems count along with their username.
    
    Returns:
        A JSON object containing:
        - username: The user's username
        - gems: The number of gems the user has
        - status: Success indicator
    """
    try:
        user = db.query(User).filter(User.account_id == current_user.account_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        return {
            "status": "success",
            "username": user.username,
            "gems": user.gems
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error retrieving gems: {str(e)}")
        return {
            "status": "error",
            "message": "An error occurred while retrieving gems",
            "error": str(e)
        }

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

@router.post("/extended-update", status_code=200)
async def update_extended_profile(
    request: Request,
    profile: ExtendedProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update extended user profile information including contact details and address.
    Username updates are not handled here - use the dedicated username update endpoint instead.
    """
    try:
        # Get the current user from database
        user = db.query(User).filter(User.account_id == current_user.account_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found")
        
        # Update profile fields (username updates are handled separately)
        if profile.first_name is not None:
            user.first_name = profile.first_name
        
        if profile.last_name is not None:
            user.last_name = profile.last_name
        
        if profile.mobile is not None:
            user.mobile = profile.mobile
            
        if profile.country_code is not None:
            user.country_code = profile.country_code
        
        if profile.gender is not None:
            # Add gender field if it doesn't exist
            if not hasattr(user, 'gender'):
                connection = db.bind.connect()
                connection.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS gender VARCHAR")
                connection.close()
            user.gender = profile.gender
        
        # Update address fields if provided
        if profile.street_1 is not None:
            user.street_1 = profile.street_1
        
        if profile.street_2 is not None:
            user.street_2 = profile.street_2
        
        if profile.suite_or_apt_number is not None:
            user.suite_or_apt_number = profile.suite_or_apt_number
        
        if profile.city is not None:
            user.city = profile.city
        
        if profile.state is not None:
            user.state = profile.state
        
        if profile.zip is not None:
            user.zip = profile.zip
        
        if profile.country is not None:
            user.country = profile.country
        
        try:
            # Commit changes to database
            db.commit()
            logging.info(f"Extended profile successfully updated for user: {user.username}")
            
            # Return success response with updated profile details
            return {
                "status": "success",
                "message": "Profile updated successfully",
                "data": {
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "mobile": user.mobile,
                    "country_code": user.country_code,
                    "gender": getattr(user, "gender", None),
                    "address": {
                        "street_1": user.street_1,
                        "street_2": user.street_2,
                        "suite_or_apt_number": user.suite_or_apt_number,
                        "city": user.city,
                        "state": user.state,
                        "zip": user.zip,
                        "country": user.country
                    },
                    "username_updated": user.username_updated
                }
            }
        except IntegrityError as e:
            db.rollback()
            error_str = str(e).lower()
            logging.error(f"Database integrity error: {error_str}")
            return {
                "status": "error",
                "message": "Database error while updating profile. Please try again.",
                "code": "DB_INTEGRITY_ERROR"
            }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error updating extended profile: {str(e)}")
        return {
            "status": "error",
            "message": f"An unexpected error occurred: {str(e)}",
            "code": "UNEXPECTED_ERROR"
        }

@router.get("/complete", status_code=200)
async def get_complete_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get the complete profile information for the current user.
    Returns all available user fields including personal info, contact details, and address.
    """
    try:
        # Get the current user from database
        user = db.query(User).filter(User.account_id == current_user.account_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found")
        
        # Format the date of birth if it exists
        dob_formatted = user.date_of_birth.isoformat() if user.date_of_birth else None
        signup_date_formatted = user.sign_up_date.isoformat() if user.sign_up_date else None
        
        # Return all user fields
        return {
            "status": "success",
            "data": {
                "account_id": user.account_id,
                "email": user.email,
                "mobile": user.mobile,
                "country_code": user.country_code,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "middle_name": user.middle_name,
                "username": user.username,
                "gender": getattr(user, "gender", None),
                "date_of_birth": dob_formatted,
                "sign_up_date": signup_date_formatted,
                "address": {
                    "street_1": user.street_1,
                    "street_2": user.street_2,
                    "suite_or_apt_number": user.suite_or_apt_number,
                    "city": user.city,
                    "state": user.state,
                    "zip": user.zip,
                    "country": user.country
                },
                "profile_pic_url": user.profile_pic_url,
                "username_updated": user.username_updated,
                "referral_code": user.referral_code,
                "is_referred": bool(user.referred_by)
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching complete profile: {str(e)}")
        return {
            "status": "error",
            "message": f"An unexpected error occurred: {str(e)}",
            "code": "UNEXPECTED_ERROR"
        }


@router.post("/change-username")
def change_username(new_username: str, user=Depends(get_current_user), db=Depends(get_db)):
    try:
        # Only allow if user hasn't changed username before (first change free)
        if user.username_updated:
            # Require purchase or return error (preserve existing logic)
            raise HTTPException(status_code=403, detail="Username change not allowed. Please purchase a username change.")
        # Update in Descope
        client.mgmt.user.update(
            user_id=user.descope_user_id,
            update_data={
                "displayName": new_username,
                "name": new_username
            }
        )
        # Update in local DB
        user.username = new_username
        user.username_updated = True
        db.commit()
        return {"success": True, "username": new_username}
    except Exception as e:
        logging.error(f"/change-username error: {e}")
        raise HTTPException(status_code=400, detail="Something went wrong")

@router.get("/summary", status_code=200)
async def get_profile_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Compact profile summary with identity, address, profile pic, and active avatar/frame.
    """
    try:
        user = db.query(User).filter(User.account_id == current_user.account_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        avatar_obj = None
        frame_obj = None

        if user.selected_avatar_id:
            avatar_obj = db.query(Avatar).filter(Avatar.id == user.selected_avatar_id).first()
        if user.selected_frame_id:
            frame_obj = db.query(Frame).filter(Frame.id == user.selected_frame_id).first()

        # Build avatar/frame objects with optional presigned URLs
        avatar_payload = None
        if avatar_obj:
            signed = None
            bucket = getattr(avatar_obj, "bucket", None)
            object_key = getattr(avatar_obj, "object_key", None)
            if bucket and object_key:
                try:
                    signed = presign_get(bucket, object_key, expires=900)
                    if not signed:
                        logging.warning(f"presign_get returned None for avatar {avatar_obj.id} with bucket={bucket}, key={object_key}")
                except Exception as e:
                    logging.error(f"Failed to presign avatar {avatar_obj.id}: {e}", exc_info=True)
            else:
                logging.debug(f"Avatar {avatar_obj.id} missing bucket/object_key: bucket={bucket}, object_key={object_key}")
            avatar_payload = {
                "id": avatar_obj.id,
                "name": avatar_obj.name,
                "url": signed,
                "mime_type": getattr(avatar_obj, "mime_type", None)
            }

        frame_payload = None
        if frame_obj:
            signed = None
            bucket = getattr(frame_obj, "bucket", None)
            object_key = getattr(frame_obj, "object_key", None)
            if bucket and object_key:
                try:
                    signed = presign_get(bucket, object_key, expires=900)
                    if not signed:
                        logging.warning(f"presign_get returned None for frame {frame_obj.id} with bucket={bucket}, key={object_key}")
                except Exception as e:
                    logging.error(f"Failed to presign frame {frame_obj.id}: {e}", exc_info=True)
            else:
                logging.debug(f"Frame {frame_obj.id} missing bucket/object_key: bucket={bucket}, object_key={object_key}")
            frame_payload = {
                "id": frame_obj.id,
                "name": frame_obj.name,
                "url": signed,
                "mime_type": getattr(frame_obj, "mime_type", None)
            }

        return {
            "status": "success",
            "data": {
                "username": user.username,
                "account_id": user.account_id,
                "email": user.email,
                "date_of_birth": user.date_of_birth.isoformat() if user.date_of_birth else None,
                "gender": getattr(user, "gender", None),
                "address1": user.street_1,
                "address2": user.street_2,
                "apt_number": user.suite_or_apt_number,
                "city": user.city,
                "state": user.state,
                "country": user.country,
                "zip": user.zip,
                "profile_pic_url": user.profile_pic_url,
                "avatar": avatar_payload,
                "frame": frame_payload,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching profile summary: {str(e)}")
        return {
            "status": "error",
            "message": f"An unexpected error occurred: {str(e)}",
            "code": "UNEXPECTED_ERROR",
        }

