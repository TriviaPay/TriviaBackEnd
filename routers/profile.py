from fastapi import APIRouter, Depends, HTTPException, status, Request, Body, Path, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime, date
import random
import string
from db import get_db
from models import User, Badge, CountryCode
from routers.dependencies import get_current_user
import logging
from utils import get_letter_profile_pic
from descope import DescopeClient
from config import DESCOPE_PROJECT_ID, DESCOPE_MANAGEMENT_KEY

router = APIRouter(prefix="/profile", tags=["Profile"])

client = DescopeClient(project_id=DESCOPE_PROJECT_ID, management_key=DESCOPE_MANAGEMENT_KEY)

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
    current_user: User = Depends(get_current_user)
):
    """Update user profile with username, DOB, country, and process referral"""
    try:
        # Get the current user from database
        user = db.query(User).filter(User.account_id == current_user.account_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found")
        
        # Check if username already exists for another user - moved up for clearer error handling
        existing_user = db.query(User).filter(
            User.username == profile.username, 
            User.account_id != current_user.account_id
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
        
        # Check if username is changing
        username_changed = profile.username != old_username
        
        # Check if user has already updated their username before
        if username_changed and user.username_updated:
            return {
                "status": "error",
                "message": "You have already used your free username update. Username cannot be changed again.",
                "code": "USERNAME_ALREADY_UPDATED"
            }
        
        # Update username - ensure it's set even if there was no previous username
        user.username = profile.username
        logging.info(f"Updating username from '{old_username}' to '{user.username}' for user with account_id: {user.account_id}")
        
        # Update profile picture if username changed and mark username as updated
        if username_changed:
            user.profile_pic_url = get_letter_profile_pic(profile.username, db)
            user.username_updated = True
            logging.info(f"Updated profile picture for user '{user.username}' based on first letter")
            logging.info(f"Marked username as updated for user '{user.username}'")
        
        # Store date_of_birth as a Date object (not DateTime)
        user.date_of_birth = profile.date_of_birth
        
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
                
                if referrer.account_id == current_user.account_id:
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
                    "country": user.country,
                    "username_updated": user.username_updated
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
    current_user: User = Depends(get_current_user)
):
    """Check if a username is available for use"""
    try:
        # Get the current user
        user = db.query(User).filter(User.account_id == current_user.account_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found")
        
        # Check if username exists for another user
        existing_user = db.query(User).filter(
            User.username == username_data.username, 
            User.account_id != current_user.account_id
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
    current_user: User = Depends(get_current_user)
):
    """Validate if a referral code is valid and can be used"""
    try:
        # Get the current user
        user = db.query(User).filter(User.account_id == current_user.account_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found")
        
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
        
        if referrer.account_id == current_user.account_id:
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
    current_user: User = Depends(get_current_user)
):
    """Process a validated profile update"""
    try:
        # Get the current user from database
        user = db.query(User).filter(User.account_id == current_user.account_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found")
        
        # Validation logic if it hasn't been skipped
        if not profile.skip_validations:
            # Check if username already exists for another user
            existing_user = db.query(User).filter(
                User.username == profile.username, 
                User.account_id != current_user.account_id
            ).first()
            
            if existing_user:
                # Log the info but return a clean, user-friendly error message
                return {
                    "status": "error",
                    "message": f"The username '{profile.username}' is already taken. Please choose a different username.",
                    "code": "USERNAME_TAKEN"
                }
        
        # Remember the old username for logging
        old_username = user.username
        
        # Check if username is changing
        username_changed = profile.username != old_username
        
        # Check if user has already updated their username before
        if username_changed and user.username_updated:
            return {
                "status": "error",
                "message": "You have already used your free username update. Username cannot be changed again.",
                "code": "USERNAME_ALREADY_UPDATED"
            }
        
        # Update username
        user.username = profile.username
        
        # Update profile picture if username changed and mark username as updated
        if username_changed:
            user.profile_pic_url = get_letter_profile_pic(profile.username, db)
            user.username_updated = True
            logging.info(f"Updated profile picture for user '{user.username}' based on first letter")
            logging.info(f"Marked username as updated for user '{user.username}'")
        
        # Update date_of_birth
        user.date_of_birth = profile.date_of_birth
        logging.info(f"Updating date_of_birth to {profile.date_of_birth} for user with account_id: {user.account_id}")
        
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
                    
                    if referrer.account_id == current_user.account_id:
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
                    "country": user.country,
                    "username_updated": user.username_updated
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
    The username field is optional since it can only be updated once.
    """
    username: Optional[str] = None
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
    Username can only be updated once - subsequent attempts will be rejected.
    """
    try:
        # Get the current user from database
        user = db.query(User).filter(User.account_id == current_user.account_id).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found")
        
        # Handle username update (if provided)
        if profile.username:
            # Check if username has already been updated before
            if user.username_updated:
                return {
                    "status": "error",
                    "message": "You have already used your free username update. Username cannot be changed again.",
                    "code": "USERNAME_ALREADY_UPDATED"
                }
            
            # Check if the username is actually changing
            if user.username != profile.username:
                # Check if username already exists for another user
                existing_user = db.query(User).filter(
                    User.username == profile.username, 
                    User.account_id != current_user.account_id
                ).first()
                
                if existing_user:
                    logging.info(f"Username '{profile.username}' is already taken by account_id: {existing_user.account_id}")
                    return {
                        "status": "error",
                        "message": f"The username '{profile.username}' is already taken. Please choose a different username.",
                        "code": "USERNAME_TAKEN"
                    }
                
                # Store old username for comparison
                old_username = user.username
                
                # Update username and mark as updated
                user.username = profile.username
                user.username_updated = True
                logging.info(f"Updating username from '{old_username}' to '{user.username}' for user with account_id: {user.account_id}")
                
                # Update profile picture based on the new username's first letter
                user.profile_pic_url = get_letter_profile_pic(profile.username, db)
                logging.info(f"Updated profile picture for user '{user.username}' based on first letter")
        
        # Update other profile fields if provided
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
                    "username": user.username,
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
                "is_referred": user.is_referred
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

@router.get("/country-codes", status_code=200)
async def get_country_codes(
    db: Session = Depends(get_db)
):
    """
    Get a list of all country calling codes with their associated flag URLs.
    This can be used to populate a country code selector in phone number fields.
    """
    try:
        # Get all country codes
        country_codes = db.query(CountryCode).order_by(CountryCode.country_name).all()
        
        # Format the response
        result = []
        for code in country_codes:
            result.append({
                "code": code.code,
                "country_name": code.country_name,
                "flag_url": code.flag_url,
                "country_iso": code.country_iso
            })
        
        return {
            "status": "success",
            "data": result
        }
    except Exception as e:
        logging.error(f"Error fetching country codes: {str(e)}")
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
        user.display_name = new_username
        user.username_updated = True
        db.commit()
        return {"success": True, "username": new_username}
    except Exception as e:
        logging.error(f"/change-username error: {e}")
        raise HTTPException(status_code=400, detail="Something went wrong")