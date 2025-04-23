from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import Optional, List
from pydantic import BaseModel, Field
from datetime import datetime, date
import random
import string
from db import get_db
from models import User, Badge
from routers.dependencies import get_current_user
import logging

router = APIRouter(prefix="/profile", tags=["Profile"])

class ProfileUpdate(BaseModel):
    username: str
    date_of_birth: date
    country: str
    referral_code: Optional[str] = None

def generate_referral_code():
    """Generate a unique 5-digit referral code"""
    return ''.join(random.choices(string.digits, k=5))

@router.post("/update", status_code=200)
async def update_profile(
    request: Request,
    profile: ProfileUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Update user profile with username, DOB, country, and process referral"""
    try:
        # Get the current user from database
        user = db.query(User).filter(User.sub == current_user['sub']).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found with sub: {current_user['sub']}")
        
        # Check if username already exists for another user - moved up for clearer error handling
        existing_user = db.query(User).filter(
            User.username == profile.username, 
            User.sub != current_user['sub']
        ).first()
        
        if existing_user:
            # Log the info but return a clean, user-friendly error message
            logging.info(f"Username '{profile.username}' is already taken by account_id: {existing_user.account_id}")
            return {
                "status": "error",
                "message": f"The username '{profile.username}' is already taken. Please choose a different username.",
                "code": "USERNAME_TAKEN"
            }
        
        # Remember the old username for logging
        old_username = user.username
        
        # Update username - ensure it's set even if there was no previous username
        user.username = profile.username
        logging.info(f"Updating username from '{old_username}' to '{user.username}' for user with sub: {user.sub}")
        
        # Store date_of_birth as a Date object (not DateTime)
        # No need to combine with time since the model now uses Date type
        user.date_of_birth = profile.date_of_birth
        logging.info(f"Updating date_of_birth to {profile.date_of_birth} for user with sub: {user.sub}")
        
        # Update country
        user.country = profile.country 
        
        # Process referral code if provided
        if profile.referral_code:
            try:
                referrer = db.query(User).filter(User.referral_code == profile.referral_code).first()
                if not referrer:
                    return {
                        "status": "error",
                        "message": f"Invalid referral code '{profile.referral_code}'. Please check and try again.",
                        "code": "INVALID_REFERRAL_CODE"
                    }
                
                if referrer.sub == current_user['sub']:
                    return {
                        "status": "error",
                        "message": "You cannot use your own referral code.",
                        "code": "SELF_REFERRAL"
                    }
                
                # Update referrer's count and mark current user as referred
                referrer.referral_count += 1
                user.referred_by = profile.referral_code
                user.is_referred = True
                logging.info(f"Successfully applied referral code: {profile.referral_code} from user {referrer.username}")
            except IntegrityError:
                db.rollback()
                return {
                    "status": "error",
                    "message": "Database error processing referral code. Please try again.",
                    "code": "DB_ERROR"
                }
            except Exception as e:
                logging.error(f"Error processing referral code: {str(e)}")
                return {
                    "status": "error",
                    "message": "Error processing referral code. Please try again.",
                    "code": "REFERRAL_ERROR"
                }
        
        # Generate unique referral code if not already present
        if not user.referral_code:
            max_attempts = 10
            for attempt in range(max_attempts):
                try:
                    new_code = generate_referral_code()
                    user.referral_code = new_code
                    db.flush()  # Try to write to DB to check for uniqueness
                    logging.info(f"Generated new referral code: {new_code} for user {user.username} on attempt {attempt+1}")
                    break
                except IntegrityError:
                    db.rollback()
                    logging.warning(f"Referral code collision on attempt {attempt+1}, trying again")
                    continue
            else:
                return {
                    "status": "error",
                    "message": "Could not generate a unique referral code. Please try again.",
                    "code": "REFERRAL_CODE_GENERATION_FAILED"
                }
        
        try:
            # Commit changes to database
            db.commit()
            logging.info(f"Profile successfully updated for user: {user.username}")
            
            # Return success response with updated profile details
            return {
                "status": "success",
                "message": "Profile updated successfully",
                "data": {
                    "username": user.username,
                    "referral_code": user.referral_code,
                    "is_referred": user.is_referred,
                    "date_of_birth": user.date_of_birth.isoformat() if user.date_of_birth else None,
                    "country": user.country
                }
            }
        except IntegrityError as e:
            db.rollback()
            error_str = str(e).lower()
            logging.error(f"Database integrity error: {error_str}")
            
            # Check if the error is specifically about username uniqueness
            if "unique" in error_str and "username" in error_str:
                return {
                    "status": "error",
                    "message": f"The username '{profile.username}' is already taken. Please choose a different username.",
                    "code": "USERNAME_TAKEN"
                }
            else:
                return {
                    "status": "error",
                    "message": "Database error while updating profile. Please try again.",
                    "code": "DB_INTEGRITY_ERROR"
                }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error updating profile: {str(e)}")
        return {
            "status": "error",
            "message": f"An unexpected error occurred: {str(e)}",
            "code": "UNEXPECTED_ERROR"
        }

@router.get("/countries")
async def get_countries():
    """Get list of all countries"""
    # You can expand this list as needed
    countries = [
        "United States", "Canada", "United Kingdom", "Australia", "India",
        "Germany", "France", "Japan", "Brazil", "Mexico", "China", "Spain",
        "Italy", "Russia", "South Korea", "Singapore", "New Zealand",
        "South Africa", "Nigeria", "Kenya", "Egypt", "Saudi Arabia",
        "United Arab Emirates", "Pakistan", "Bangladesh", "Malaysia",
        "Indonesia", "Philippines", "Vietnam", "Thailand"
    ]
    return {"countries": sorted(countries)}

# New endpoints below

class UsernameCheck(BaseModel):
    username: str

class ReferralCheck(BaseModel):
    referral_code: str

@router.post("/check-username")
async def check_username_availability(
    username_data: UsernameCheck,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Check if a username is available for use"""
    try:
        # Get the current user
        user = db.query(User).filter(User.sub == current_user['sub']).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found with sub: {current_user['sub']}")
        
        # Check if username exists for another user
        existing_user = db.query(User).filter(
            User.username == username_data.username, 
            User.sub != current_user['sub']
        ).first()
        
        if existing_user:
            # Username is taken
            logging.info(f"Username '{username_data.username}' is already taken by account_id: {existing_user.account_id}")
            return {
                "status": "error",
                "message": f"The username '{username_data.username}' is already taken. Please choose a different username.",
                "code": "USERNAME_TAKEN",
                "available": False
            }
        
        # Username is available
        return {
            "status": "success",
            "message": f"The username '{username_data.username}' is available.",
            "available": True
        }
            
    except Exception as e:
        logging.error(f"Error checking username availability: {str(e)}")
        return {
            "status": "error",
            "message": f"An error occurred while checking username availability: {str(e)}",
            "code": "CHECK_USERNAME_ERROR",
            "available": False
        }

@router.post("/validate-referral")
async def validate_referral_code(
    referral_data: ReferralCheck,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Validate if a referral code is valid and can be used"""
    try:
        # Get the current user
        user = db.query(User).filter(User.sub == current_user['sub']).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found with sub: {current_user['sub']}")
        
        # Check if user already has a referral code applied
        if user.is_referred:
            return {
                "status": "error",
                "message": "You have already used a referral code.",
                "code": "ALREADY_REFERRED",
                "valid": False
            }
        
        # Find the referrer
        referrer = db.query(User).filter(User.referral_code == referral_data.referral_code).first()
        
        if not referrer:
            return {
                "status": "error",
                "message": f"Invalid referral code '{referral_data.referral_code}'. Please check and try again.",
                "code": "INVALID_REFERRAL_CODE",
                "valid": False
            }
        
        if referrer.sub == current_user['sub']:
            return {
                "status": "error",
                "message": "You cannot use your own referral code.",
                "code": "SELF_REFERRAL",
                "valid": False
            }
        
        # Referral code is valid
        return {
            "status": "success",
            "message": "Referral code is valid.",
            "referrer_username": referrer.username if referrer.username else "Anonymous User",
            "valid": True
        }
            
    except Exception as e:
        logging.error(f"Error validating referral code: {str(e)}")
        return {
            "status": "error",
            "message": f"An error occurred while validating the referral code: {str(e)}",
            "code": "VALIDATE_REFERRAL_ERROR",
            "valid": False
        }

class ProfileFinalUpdate(BaseModel):
    username: str
    date_of_birth: date
    country: str
    referral_code: Optional[str] = None
    # Add flag to skip validation if already validated in previous steps
    skip_validations: bool = Field(default=False, description="Set to true if username and referral code were already validated")

@router.post("/perform-update", status_code=200)
async def perform_profile_update(
    request: Request,
    profile: ProfileFinalUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Perform the actual profile update after validation"""
    try:
        # Get the current user from database
        user = db.query(User).filter(User.sub == current_user['sub']).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found with sub: {current_user['sub']}")
        
        # Check username availability unless skip_validations is True
        if not profile.skip_validations:
            existing_user = db.query(User).filter(
                User.username == profile.username, 
                User.sub != current_user['sub']
            ).first()
            
            if existing_user:
                logging.info(f"Username '{profile.username}' is already taken by account_id: {existing_user.account_id}")
                return {
                    "status": "error",
                    "message": f"The username '{profile.username}' is already taken. Please choose a different username.",
                    "code": "USERNAME_TAKEN"
                }
        
        # Remember old username for logging
        old_username = user.username
        
        # Update username
        user.username = profile.username
        logging.info(f"Updating username from '{old_username}' to '{user.username}' for user with sub: {user.sub}")
        
        # Update date_of_birth
        user.date_of_birth = profile.date_of_birth
        logging.info(f"Updating date_of_birth to {profile.date_of_birth} for user with sub: {user.sub}")
        
        # Update country
        user.country = profile.country 
        
        # Process referral code if provided
        if profile.referral_code and not user.is_referred:
            try:
                # Skip validation if already done
                if not profile.skip_validations:
                    referrer = db.query(User).filter(User.referral_code == profile.referral_code).first()
                    if not referrer:
                        return {
                            "status": "error",
                            "message": f"Invalid referral code '{profile.referral_code}'. Please check and try again.",
                            "code": "INVALID_REFERRAL_CODE"
                        }
                    
                    if referrer.sub == current_user['sub']:
                        return {
                            "status": "error",
                            "message": "You cannot use your own referral code.",
                            "code": "SELF_REFERRAL"
                        }
                else:
                    # Just retrieve the referrer
                    referrer = db.query(User).filter(User.referral_code == profile.referral_code).first()
                
                if referrer:
                    # Update referrer's count and mark current user as referred
                    referrer.referral_count += 1
                    user.referred_by = profile.referral_code
                    user.is_referred = True
                    logging.info(f"Successfully applied referral code: {profile.referral_code} from user {referrer.username}")
            except IntegrityError:
                db.rollback()
                return {
                    "status": "error",
                    "message": "Database error processing referral code. Please try again.",
                    "code": "DB_ERROR"
                }
            except Exception as e:
                logging.error(f"Error processing referral code: {str(e)}")
                return {
                    "status": "error",
                    "message": "Error processing referral code. Please try again.",
                    "code": "REFERRAL_ERROR"
                }
        
        # Generate unique referral code if not already present
        if not user.referral_code:
            max_attempts = 10
            for attempt in range(max_attempts):
                try:
                    new_code = generate_referral_code()
                    user.referral_code = new_code
                    db.flush()  # Try to write to DB to check for uniqueness
                    logging.info(f"Generated new referral code: {new_code} for user {user.username} on attempt {attempt+1}")
                    break
                except IntegrityError:
                    db.rollback()
                    logging.warning(f"Referral code collision on attempt {attempt+1}, trying again")
                    continue
            else:
                return {
                    "status": "error",
                    "message": "Could not generate a unique referral code. Please try again.",
                    "code": "REFERRAL_CODE_GENERATION_FAILED"
                }
        
        try:
            # Commit changes to database
            db.commit()
            logging.info(f"Profile successfully updated for user: {user.username}")
            
            # Return success response with updated profile details
            return {
                "status": "success",
                "message": "Profile updated successfully",
                "data": {
                    "username": user.username,
                    "referral_code": user.referral_code,
                    "is_referred": user.is_referred,
                    "date_of_birth": user.date_of_birth.isoformat() if user.date_of_birth else None,
                    "country": user.country
                }
            }
        except IntegrityError as e:
            db.rollback()
            error_str = str(e).lower()
            logging.error(f"Database integrity error: {error_str}")
            
            # Check if the error is specifically about username uniqueness
            if "unique" in error_str and "username" in error_str:
                return {
                    "status": "error",
                    "message": f"The username '{profile.username}' is already taken. Please choose a different username.",
                    "code": "USERNAME_TAKEN"
                }
            else:
                return {
                    "status": "error",
                    "message": "Database error while updating profile. Please try again.",
                    "code": "DB_INTEGRITY_ERROR"
                }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error updating profile: {str(e)}")
        return {
            "status": "error",
            "message": f"An unexpected error occurred: {str(e)}",
            "code": "UNEXPECTED_ERROR"
        }

# Badge related models
class BadgeAssignment(BaseModel):
    badge_id: str

@router.get("/badges", response_model=List[dict])
async def get_user_badges(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Get the current badge assigned to the user and all available badges
    """
    try:
        # Get the current user
        user = db.query(User).filter(User.sub == current_user['sub']).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found with sub: {current_user['sub']}")
        
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
    current_user: dict = Depends(get_current_user)
):
    """
    Assign a badge to the user
    """
    try:
        # Get the current user
        user = db.query(User).filter(User.sub == current_user['sub']).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found with sub: {current_user['sub']}")
        
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
    current_user: dict = Depends(get_current_user)
):
    """
    Remove the current badge from the user
    """
    try:
        # Get the current user
        user = db.query(User).filter(User.sub == current_user['sub']).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found with sub: {current_user['sub']}")
        
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