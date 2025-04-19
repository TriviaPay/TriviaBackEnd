from fastapi import APIRouter, Depends, HTTPException, status, Request, Path, Body
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime, date
import random
import string
import os
import io
from PIL import Image, ImageDraw, ImageFont
import base64
from db import get_db
from models import User, Badge, Avatar, Frame, UserAvatar, UserFrame
from routers.dependencies import get_current_user
import logging

router = APIRouter(prefix="/profile", tags=["Profile"])

# Response schema for profile information
class ProfileInfoResponse(BaseModel):
    username: str
    account_id: int
    email: str
    display_type: str  # "avatar" or "letter"
    display_image_url: str  # The URL of the profile picture (either avatar or letter-based)
    selected_avatar_id: Optional[str] = None
    frame_url: Optional[str] = None
    selected_frame_id: Optional[str] = None
    badge_name: Optional[str] = None
    badge_image_url: Optional[str] = None
    badge_id: Optional[str] = None
    gems: int
    streaks: int
    wallet_balance: float
    
# Schema for selecting profile display type
class ProfileDisplaySelect(BaseModel):
    display_type: str = Field(..., description="The display type to use: 'avatar' or 'letter'")

@router.get("/info", response_model=ProfileInfoResponse)
async def get_profile_info(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Get complete user profile information including avatar, frame, and badge details.
    Only returns one profile picture URL based on selected display type.
    """
    user = db.query(User).filter(User.sub == current_user['sub']).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Determine display type and image URL
    display_type = "letter" if user.profile_pic_url else "avatar"
    display_image_url = user.profile_pic_url
    
    # If display type is avatar, get the avatar URL
    if display_type == "avatar":
        if user.selected_avatar_id:
            avatar = db.query(Avatar).filter(Avatar.id == user.selected_avatar_id).first()
            if avatar:
                display_image_url = avatar.image_url
        
        # If no avatar is selected or found, generate a letter-based image
        if not display_image_url and user.username:
            display_type = "letter"
            first_letter = user.username[0]
            display_image_url = generate_letter_image(first_letter)
            # Note: We don't save this to the database here
    
    # Get frame information if selected
    frame_url = None
    if user.selected_frame_id:
        frame = db.query(Frame).filter(Frame.id == user.selected_frame_id).first()
        if frame:
            frame_url = frame.image_url
    
    # Get badge information
    badge_name = None
    badge_image_url = None
    if user.badge_id:
        badge = db.query(Badge).filter(Badge.id == user.badge_id).first()
        if badge:
            badge_name = badge.name
            badge_image_url = badge.image_url
    
    return {
        "username": user.username or "",
        "account_id": user.account_id,
        "email": user.email,
        "display_type": display_type,
        "display_image_url": display_image_url or "",
        "selected_avatar_id": user.selected_avatar_id,
        "frame_url": frame_url,
        "selected_frame_id": user.selected_frame_id,
        "badge_name": badge_name,
        "badge_image_url": user.badge_image_url or badge_image_url,
        "badge_id": user.badge_id,
        "gems": user.gems,
        "streaks": user.streaks,
        "wallet_balance": user.wallet_balance
    }

# Helper function to generate a letter-based profile picture
def generate_letter_image(letter, size=200, bg_color=None, text_color=(255, 255, 255)):
    """
    Generate a profile picture with the first letter of username.
    
    Args:
        letter: The letter to display (usually first letter of username)
        size: Size of the square image in pixels
        bg_color: Background color as RGB tuple. If None, a random color is generated.
        text_color: Text color as RGB tuple.
    
    Returns:
        Base64 encoded PNG image
    """
    if not bg_color:
        # Generate a random but visually pleasing background color
        # Using pastel colors for a friendly appearance
        r = random.randint(100, 200)
        g = random.randint(100, 200)
        b = random.randint(100, 200)
        bg_color = (r, g, b)
    
    # Create a new image with the given background color
    image = Image.new('RGB', (size, size), bg_color)
    draw = ImageDraw.Draw(image)
    
    # Try to load a font, fall back to default if not available
    try:
        # Using a larger font size to make the letter prominent
        font_size = int(size * 0.6)
        font = ImageFont.truetype("Arial", font_size)
    except IOError:
        # If Arial is not available, use default font
        font = ImageFont.load_default()
    
    # Get letter dimensions to center it
    letter = letter.upper()  # Convert to uppercase for better visibility
    # For default font, estimate text size
    w, h = draw.textsize(letter, font=font) if hasattr(draw, 'textsize') else (font_size * 0.6, font_size)
    
    # Draw the letter centered on the image
    position = ((size - w) / 2, (size - h) / 2 - h * 0.1)  # Slight upward adjustment for visual center
    draw.text(position, letter, fill=text_color, font=font)
    
    # Convert to base64 encoded PNG
    buffered = io.BytesIO()
    image.save(buffered, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode('utf-8')}"

@router.post("/display-select", response_model=Dict[str, Any])
async def select_profile_display(
    selection: ProfileDisplaySelect,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Select whether to display an avatar or letter-based profile picture.
    If letter is selected, it generates a profile picture based on the first letter of username.
    """
    user = db.query(User).filter(User.sub == current_user['sub']).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if not user.username:
        raise HTTPException(status_code=400, detail="Username is required to generate a letter profile picture")
    
    if selection.display_type not in ["avatar", "letter"]:
        raise HTTPException(status_code=400, detail="Invalid display type. Must be 'avatar' or 'letter'")
    
    if selection.display_type == "avatar":
        # Clear profile_pic_url if avatar is selected
        user.profile_pic_url = None
        
        # Ensure user has a selected avatar
        if not user.selected_avatar_id:
            # Try to find a default avatar to assign
            default_avatar = db.query(Avatar).filter(Avatar.is_default == True).first()
            if default_avatar:
                user.selected_avatar_id = default_avatar.id
            else:
                raise HTTPException(status_code=400, detail="No avatar selected and no default avatar available")
        
        avatar = db.query(Avatar).filter(Avatar.id == user.selected_avatar_id).first()
        if not avatar:
            raise HTTPException(status_code=404, detail="Selected avatar not found")
        
        db.commit()
        
        return {
            "status": "success",
            "message": "Avatar display selected successfully",
            "display_type": "avatar",
            "avatar_url": avatar.image_url,
            "avatar_id": avatar.id
        }
    
    else:  # letter
        # Generate letter image based on first letter of username
        if not user.username or len(user.username) < 1:
            raise HTTPException(status_code=400, detail="Username is required and must be at least 1 character")
        
        first_letter = user.username[0]
        letter_image_base64 = generate_letter_image(first_letter)
        
        # Save the base64 image URL to user's profile_pic_url
        user.profile_pic_url = letter_image_base64
        # Clear selected avatar to ensure letter is used
        user.selected_avatar_id = None
        
        db.commit()
        
        return {
            "status": "success",
            "message": "Letter profile picture selected successfully",
            "display_type": "letter",
            "profile_pic_url": letter_image_base64
        }

@router.get("/generate-letter-pic")
async def generate_letter_profile_pic(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Generate a letter-based profile picture from the user's username without saving it.
    """
    user = db.query(User).filter(User.sub == current_user['sub']).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if not user.username or len(user.username) < 1:
        raise HTTPException(status_code=400, detail="Username is required and must be at least 1 character")
    
    first_letter = user.username[0]
    letter_image_base64 = generate_letter_image(first_letter)
    
    return {
        "username": user.username,
        "first_letter": first_letter,
        "profile_pic_url": letter_image_base64
    }

class ProfileUpdate(BaseModel):
    username: str
    date_of_birth: date
    country: str
    referral_code: Optional[str] = None

def generate_referral_code():
    """Generate a unique 5-digit referral code"""
    return ''.join(random.choices(string.digits, k=5))

@router.post("/update")
async def update_profile(
    profile: ProfileUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Update the user profile with the provided information.
    Updates username, date of birth, country, and processes referral code.
    Also regenerates letter-based profile pic if username changes and user is using letter profile.
    """
    try:
        # Get the user from database
        user = db.query(User).filter(User.sub == current_user['sub']).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found with sub: {current_user['sub']}")
        
        # Check if username exists for another user
        existing_user = db.query(User).filter(
            User.username == profile.username, 
            User.sub != current_user['sub']
        ).first()
        
        if existing_user:
            # Username is taken by another user
            return {
                "status": "error",
                "message": f"The username '{profile.username}' is already taken. Please choose a different username.",
                "code": "USERNAME_TAKEN"
            }
        
        # Update username and check if it changed
        old_username = user.username
        username_changed = old_username != profile.username
        
        # Update username
        user.username = profile.username
        logging.info(f"Updating username from '{old_username}' to '{user.username}' for user with sub: {user.sub}")
        
        # If username changed and using letter-based profile pic, regenerate it
        if username_changed and user.profile_pic_url and not user.selected_avatar_id:
            first_letter = user.username[0]
            user.profile_pic_url = generate_letter_image(first_letter)
            logging.info(f"Regenerated letter-based profile picture for user {user.username} with first letter: {first_letter}")
        
        # Update date of birth
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

@router.post("/final-update", status_code=200)
async def profile_final_update(
    profile: ProfileFinalUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Finalize user profile updates with ability to skip validations.
    Also regenerates letter-based profile pic if username changes and user is using letter profile.
    """
    try:
        # Get the current user from database
        user = db.query(User).filter(User.sub == current_user['sub']).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found with sub: {current_user['sub']}")
        
        # Check username uniqueness if we're not skipping validations
        if not profile.skip_validations:
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
        
        # Remember the old username for logging and checking changes
        old_username = user.username
        username_changed = old_username != profile.username
        
        # Update username - ensure it's set even if there was no previous username
        user.username = profile.username
        logging.info(f"Updating username from '{old_username}' to '{user.username}' for user with sub: {user.sub}")
        
        # If username changed and using letter-based profile pic, regenerate it
        if username_changed and user.profile_pic_url and not user.selected_avatar_id:
            first_letter = user.username[0]
            user.profile_pic_url = generate_letter_image(first_letter)
            logging.info(f"Regenerated letter-based profile picture for user {user.username} with first letter: {first_letter}")
            
        # Store date_of_birth as a Date object (not DateTime)
        user.date_of_birth = profile.date_of_birth
        
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