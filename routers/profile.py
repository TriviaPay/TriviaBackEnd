from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from datetime import datetime, date
from sqlalchemy import and_, or_, text
import uuid
import os
from db import get_db
from models import (
    User, TriviaModeConfig, Avatar, Frame, UserSubscription, SubscriptionPlan,
    TriviaBronzeModeLeaderboard, TriviaSilverModeLeaderboard
)
from utils.storage import presign_get, upload_file, delete_file
from routers.dependencies import get_current_user
import logging
from utils import get_letter_profile_pic
from descope import DescopeClient
from datetime import datetime
from sqlalchemy import and_, or_
from config import (
    DESCOPE_PROJECT_ID,
    DESCOPE_MANAGEMENT_KEY,
    DESCOPE_JWT_LEEWAY,
    AWS_PROFILE_PIC_BUCKET,
    REFERRAL_APP_LINK,
)
from utils.referrals import get_unique_referral_code
from utils.user_level_service import get_level_progress
from utils.trivia_mode_service import get_active_draw_date, get_today_in_app_timezone
from utils.subscription_service import check_mode_access, get_modes_access_status

router = APIRouter(prefix="/profile", tags=["Profile"])

client = DescopeClient(project_id=DESCOPE_PROJECT_ID, management_key=DESCOPE_MANAGEMENT_KEY, jwt_validation_leeway=DESCOPE_JWT_LEEWAY)

# Cache schema check to avoid per-request DDL.
_gender_column_checked = False

# ======== Helper Functions ========

def _ensure_gender_column(db: Session) -> None:
    global _gender_column_checked
    if _gender_column_checked:
        return
    connection = None
    try:
        connection = db.bind.connect()
        connection.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS gender VARCHAR"))
    except Exception as e:
        logging.error(f"Failed to ensure gender column exists: {e}")
    finally:
        if connection is not None:
            connection.close()
        _gender_column_checked = True

def get_badge_info(user: User, db: Session) -> Optional[Dict[str, Any]]:
    """
    Get badge information for a user (achievement badge).
    Returns badge id, name, and image_url (public S3 URL).
    Badge functionality is now stored in TriviaModeConfig.
    
    Args:
        user: User object with badge_id (which now references mode_id)
        db: Database session
        
    Returns:
        Dictionary with badge info or None if user has no badge
    """
    if not user.badge_id:
        return None
    
    # badge_id now references mode_id in trivia_mode_config
    mode_config = db.query(TriviaModeConfig).filter(TriviaModeConfig.mode_id == user.badge_id).first()
    if not mode_config or not mode_config.badge_image_url:
        return None
    
    return {
        "id": mode_config.mode_id,
        "name": mode_config.mode_name,
        "image_url": mode_config.badge_image_url  # Public URL, no presigning needed
    }


def get_recent_draw_earnings(user: User, db: Session) -> float:
    """
    Get the amount earned by the user in the most recent completed draw.
    Checks both bronze and silver mode leaderboards.
    
    Args:
        user: User object
        db: Database session
        
    Returns:
        Total amount earned in the most recent draw (0 if no earnings)
    """
    try:
        # Get the most recent completed draw date
        active_date = get_active_draw_date()
        today = get_today_in_app_timezone()
        
        # Determine the draw date for winners
        if active_date == today:
            # After draw time, show today's completed draw
            draw_date = active_date
        else:
            # Before draw time, show yesterday's completed draw
            draw_date = active_date
        
        # Check bronze mode leaderboard
        bronze_entry = db.query(TriviaBronzeModeLeaderboard).filter(
            TriviaBronzeModeLeaderboard.account_id == user.account_id,
            TriviaBronzeModeLeaderboard.draw_date == draw_date
        ).first()
        
        # Check silver mode leaderboard
        silver_entry = db.query(TriviaSilverModeLeaderboard).filter(
            TriviaSilverModeLeaderboard.account_id == user.account_id,
            TriviaSilverModeLeaderboard.draw_date == draw_date
        ).first()
        
        # Sum up earnings from both modes
        total_earnings = 0.0
        if bronze_entry:
            total_earnings += float(bronze_entry.money_awarded or 0)
        if silver_entry:
            total_earnings += float(silver_entry.money_awarded or 0)
        
        return round(total_earnings, 2)
    except Exception as e:
        logging.error(f"Error getting recent draw earnings for user {user.account_id}: {str(e)}")
        return 0.0


def get_subscription_badges(user: User, db: Session) -> List[Dict[str, Any]]:
    """
    Get subscription badge URLs for a user based on their active subscriptions.
    Returns a list of badge info dictionaries for bronze ($5) and silver ($10) subscriptions.
    
    Args:
        user: User object
        db: Database session
        
    Returns:
        List of dictionaries with badge info (id, name, image_url) for each active subscription
    """
    subscription_badges = []
    
    active_subscriptions = db.query(
        SubscriptionPlan.unit_amount_minor,
        SubscriptionPlan.price_usd
    ).join(UserSubscription).filter(
        and_(
            UserSubscription.user_id == user.account_id,
            UserSubscription.status == 'active',
            UserSubscription.current_period_end > datetime.utcnow(),
            or_(
                SubscriptionPlan.unit_amount_minor.in_([500, 1000]),
                SubscriptionPlan.price_usd.in_([5.0, 10.0])
            )
        )
    ).all()

    has_bronze = any(
        unit_amount_minor == 500 or price_usd == 5.0
        for unit_amount_minor, price_usd in active_subscriptions
    )
    has_silver = any(
        unit_amount_minor == 1000 or price_usd == 10.0
        for unit_amount_minor, price_usd in active_subscriptions
    )

    badge_map = {}
    if has_bronze or has_silver:
        badge_candidates = ['bronze', 'bronze_badge', 'brone_badge', 'brone', 'silver', 'silver_badge']
        badges = db.query(TriviaModeConfig).filter(
            TriviaModeConfig.mode_id.in_(badge_candidates),
            TriviaModeConfig.badge_image_url.isnot(None)
        ).all()
        badge_map = {badge.mode_id: badge for badge in badges}

    bronze_badge = None
    if has_bronze:
        for mode_id in ['bronze', 'bronze_badge', 'brone_badge', 'brone']:
            bronze_badge = badge_map.get(mode_id)
            if bronze_badge:
                break
        if not bronze_badge:
            bronze_badge = db.query(TriviaModeConfig).filter(
                TriviaModeConfig.mode_name.ilike('%bronze%'),
                TriviaModeConfig.badge_image_url.isnot(None)
            ).first()

    if bronze_badge and bronze_badge.badge_image_url:
        subscription_badges.append({
            "id": bronze_badge.mode_id,
            "name": bronze_badge.mode_name,
            "image_url": bronze_badge.badge_image_url,
            "subscription_type": "bronze",
            "price": 5.0
        })

    silver_badge = None
    if has_silver:
        for mode_id in ['silver', 'silver_badge']:
            silver_badge = badge_map.get(mode_id)
            if silver_badge:
                break
        if not silver_badge:
            silver_badge = db.query(TriviaModeConfig).filter(
                TriviaModeConfig.mode_name.ilike('%silver%'),
                TriviaModeConfig.badge_image_url.isnot(None)
            ).first()

    if silver_badge and silver_badge.badge_image_url:
        subscription_badges.append({
            "id": silver_badge.mode_id,
            "name": silver_badge.mode_name,
            "image_url": silver_badge.badge_image_url,
            "subscription_type": "silver",
            "price": 10.0
        })
    
    return subscription_badges

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
        
        # Get badge information (achievement badge)
        badge_info = get_badge_info(user, db)
        
        # Get subscription badges
        subscription_badges = get_subscription_badges(user, db)
        
        # Get recent draw earnings
        recent_draw_earnings = get_recent_draw_earnings(user, db)
        
        return {
            "status": "success",
            "username": user.username,
            "gems": user.gems,
            "badge": badge_info,  # Achievement badge
            "subscription_badges": subscription_badges,  # Array of subscription badge URLs
            "recent_draw_earnings": recent_draw_earnings  # Amount earned in most recent draw
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error("Error retrieving gems", exc_info=True)
        return {
            "status": "error",
            "message": "An error occurred while retrieving gems",
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
            _ensure_gender_column(db)
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
            
            # Get badge information
            badge_info = get_badge_info(user, db)
            
            # Get wallet balance (trivia coins) - use wallet_balance_minor if available, otherwise convert wallet_balance
            wallet_balance_minor = user.wallet_balance_minor if hasattr(user, 'wallet_balance_minor') and user.wallet_balance_minor is not None else int((user.wallet_balance or 0) * 100)
            wallet_balance_usd = wallet_balance_minor / 100.0 if wallet_balance_minor else 0.0
            
            # Get recent draw earnings
            recent_draw_earnings = get_recent_draw_earnings(user, db)
            
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
                    "username_updated": user.username_updated,
                    "badge": badge_info,
                    "total_gems": user.gems or 0,  # Total gem count
                    "total_trivia_coins": wallet_balance_usd,  # Total trivia coins (wallet balance in USD)
                    "level": user.level if user.level else 1,  # User level (increases by 1 for every 100 correct answers)
                    "level_progress": get_level_progress(user, db)["progress"],  # Level progress string (e.g., "2/100")
                    "recent_draw_earnings": recent_draw_earnings  # Amount earned in most recent draw
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
        logging.error("Error updating extended profile", exc_info=True)
        return {
            "status": "error",
            "message": "An unexpected error occurred",
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
        def _safe_iso_format(value):
            """Safely format a date/datetime value to ISO format string."""
            if not value:
                return None
            if isinstance(value, str):
                return value  # Already a string
            if hasattr(value, "isoformat"):
                return value.isoformat()
            return str(value)
        
        dob_formatted = _safe_iso_format(user.date_of_birth)
        signup_date_formatted = _safe_iso_format(user.sign_up_date)
        
        # Get badge information (achievement badge)
        badge_info = get_badge_info(user, db)
        
        # Get subscription badges
        subscription_badges = get_subscription_badges(user, db)
        
        # Get wallet balance (trivia coins) - use wallet_balance_minor if available, otherwise convert wallet_balance
        wallet_balance_minor = user.wallet_balance_minor if hasattr(user, 'wallet_balance_minor') and user.wallet_balance_minor is not None else int((user.wallet_balance or 0) * 100)
        wallet_balance_usd = wallet_balance_minor / 100.0 if wallet_balance_minor else 0.0
        
        # Get recent draw earnings
        recent_draw_earnings = get_recent_draw_earnings(user, db)
        
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
                "is_referred": bool(user.referred_by),
                "badge": badge_info,  # Achievement badge
                "subscription_badges": subscription_badges,  # Array of subscription badge URLs
                "total_gems": user.gems or 0,  # Total gem count
                "total_trivia_coins": wallet_balance_usd,  # Total trivia coins (wallet balance in USD)
                "level": user.level if user.level else 1,  # User level (increases by 1 for every 100 correct answers)
                "level_progress": get_level_progress(user, db)["progress"],  # Level progress string (e.g., "2/100")
                "recent_draw_earnings": recent_draw_earnings  # Amount earned in most recent draw
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error("Error fetching complete profile", exc_info=True)
        return {
            "status": "error",
            "message": "An unexpected error occurred",
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

        # Get badge information (achievement badge)
        badge_info = get_badge_info(user, db)
        
        # Get subscription badges
        subscription_badges = get_subscription_badges(user, db)
        
        # Get wallet balance (trivia coins) - use wallet_balance_minor if available, otherwise convert wallet_balance
        wallet_balance_minor = user.wallet_balance_minor if hasattr(user, 'wallet_balance_minor') and user.wallet_balance_minor is not None else int((user.wallet_balance or 0) * 100)
        wallet_balance_usd = wallet_balance_minor / 100.0 if wallet_balance_minor else 0.0
        
        # Get recent draw earnings
        recent_draw_earnings = get_recent_draw_earnings(user, db)
        
        # Determine which profile picture type is active
        profile_pic_type = None
        if user.profile_pic_url:
            profile_pic_type = "custom"  # Custom uploaded profile picture
        elif user.selected_avatar_id:
            profile_pic_type = "avatar"  # Purchased avatar selected
        else:
            profile_pic_type = "default"  # Default letter-based profile picture

        def _safe_iso(value):
            if not value:
                return None
            if hasattr(value, "isoformat"):
                return value.isoformat()
            if isinstance(value, str):
                return value
            return str(value)

        return {
            "status": "success",
            "data": {
                "username": user.username,
                "account_id": user.account_id,
                "email": user.email,
                "date_of_birth": _safe_iso(user.date_of_birth),
                "gender": getattr(user, "gender", None),
                "address1": user.street_1,
                "address2": user.street_2,
                "apt_number": user.suite_or_apt_number,
                "city": user.city,
                "state": user.state,
                "country": user.country,
                "zip": user.zip,
                "profile_pic_url": user.profile_pic_url,
                "profile_pic_type": profile_pic_type,  # "custom", "avatar", or "default"
                "avatar": avatar_payload,
                "frame": frame_payload,
                "badge": badge_info,  # Achievement badge
                "subscription_badges": subscription_badges,  # Array of subscription badge URLs
                "total_gems": user.gems or 0,  # Total gem count
                "total_trivia_coins": wallet_balance_usd,  # Total trivia coins (wallet balance in USD)
                "level": user.level if user.level else 1,  # User level (increases by 1 for every 100 correct answers)
                "level_progress": get_level_progress(user, db)["progress"],  # Level progress string (e.g., "2/100")
                "recent_draw_earnings": recent_draw_earnings  # Amount earned in most recent draw
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error("Error fetching profile summary", exc_info=True)
        return {
            "status": "error",
            "message": "An unexpected error occurred",
            "code": "UNEXPECTED_ERROR",
        }


@router.post("/send-referral", status_code=200)
async def send_referral(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return the user's referral code and a simple shareable message.
    """
    try:
        user = db.query(User).filter(User.account_id == current_user.account_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if not user.referral_code:
            user.referral_code = get_unique_referral_code(db)
            db.commit()
            db.refresh(user)

        share_text = f"Send code {user.referral_code} to friends so they can join TriviaPay."
        logging.info(
            f"[REFERRAL] Sharing code {user.referral_code} for user {user.account_id} ({user.email})"
        )

        return {
            "status": "success",
            "message": "Referral code ready to share",
            "data": {
                "referral_code": user.referral_code,
                "share_text": share_text,
                "app_link": REFERRAL_APP_LINK,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error preparing referral invite: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Unable to prepare referral invite. Please try again later.",
        )

@router.post("/upload-profile-pic", status_code=200)
async def upload_profile_picture(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Upload a custom profile picture to S3.
    This will clear any selected avatar (only one can be active at a time).
    
    Accepts image files (PNG, JPEG, JPG, GIF, WebP).
    """
    try:
        # Check if bucket is configured
        if not AWS_PROFILE_PIC_BUCKET:
            raise HTTPException(
                status_code=500,
                detail="Profile picture upload is not configured. Please contact support."
            )
        
        # Validate file type
        allowed_types = ["image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"]
        if file.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type. Allowed types: {', '.join(allowed_types)}"
            )
        
        # Validate file size (max 5MB)
        file_content = await file.read()
        max_size = 5 * 1024 * 1024  # 5MB
        if len(file_content) > max_size:
            raise HTTPException(
                status_code=400,
                detail="File size exceeds maximum allowed size of 5MB"
            )
        
        # Determine file extension from content type
        extension_map = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp"
        }
        extension = extension_map.get(file.content_type, "jpg")
        
        # Generate unique S3 key for the profile picture
        # Format: profile_pic/{account_id}.{extension} or profile_pic/{email_safe}.{extension}
        # Use account_id as primary identifier, fallback to email if needed
        # Always use the same extension (jpg) to ensure override behavior
        if current_user.account_id:
            # Use account_id for uniqueness
            identifier = str(current_user.account_id)
        elif current_user.email:
            # Fallback to email (sanitize for S3 key)
            identifier = current_user.email.replace("@", "_at_").replace(".", "_")
        else:
            # Last resort: use UUID
            identifier = str(uuid.uuid4())
        
        # Always use .jpg extension to ensure uploads override previous files
        # This prevents multiple files per user (e.g., user.jpg and user.png)
        s3_key = f"profile_pic/{identifier}.jpg"
        
        # Delete any old profile picture files with different extensions
        # This ensures we don't have orphaned files (e.g., user.png when user.jpg exists)
        old_extensions = ["png", "jpeg", "gif", "webp"]
        for ext in old_extensions:
            old_key = f"profile_pic/{identifier}.{ext}"
            if old_key != s3_key:  # Don't delete the file we're about to upload
                delete_file(bucket=AWS_PROFILE_PIC_BUCKET, key=old_key)
        
        # Upload to S3
        upload_success = upload_file(
            bucket=AWS_PROFILE_PIC_BUCKET,
            key=s3_key,
            file_content=file_content,
            content_type=file.content_type
        )
        
        if not upload_success:
            raise HTTPException(
                status_code=500,
                detail="Failed to upload profile picture. Please try again."
            )
        
        # Generate presigned URL for the uploaded image (valid for 1 year)
        profile_pic_url = presign_get(
            bucket=AWS_PROFILE_PIC_BUCKET,
            key=s3_key,
            expires=31536000  # 1 year in seconds
        )
        
        if not profile_pic_url:
            # Fallback: construct public URL if presigning fails
            # This assumes the bucket allows public reads (or use CloudFront)
            bucket_region = os.getenv("AWS_REGION", "us-east-2")
            profile_pic_url = f"https://{AWS_PROFILE_PIC_BUCKET}.s3.{bucket_region}.amazonaws.com/{s3_key}"
        
        # Get the user from database
        user = db.query(User).filter(User.account_id == current_user.account_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Clear selected avatar (only one can be active at a time)
        user.selected_avatar_id = None
        
        # Update profile picture URL
        user.profile_pic_url = profile_pic_url
        
        # Commit changes
        db.commit()
        
        # Get badge information
        badge_info = get_badge_info(user, db)
        
        logging.info(f"Profile picture uploaded successfully for user {user.account_id}")
        
        return {
            "status": "success",
            "message": "Profile picture uploaded successfully",
            "data": {
                "profile_pic_url": profile_pic_url,
                "profile_pic_type": "custom",  # Indicates this is a custom upload, not an avatar
                "badge": badge_info
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error("Error uploading profile picture", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while uploading profile picture"
        )


@router.get("/modes/status", status_code=200)
async def get_all_modes_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get combined subscription status for all 3 modes (free, bronze, silver).
    Shows which modes the current user has access to.
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    access_map = get_modes_access_status(db, user, ['free_mode', 'bronze', 'silver'])
    free_mode_access = access_map.get('free_mode', {})
    bronze_mode_access = access_map.get('bronze', {})
    silver_mode_access = access_map.get('silver', {})
    
    return {
        'free_mode': {
            'has_access': free_mode_access['has_access'],
            'subscription_status': free_mode_access.get('subscription_status', 'not_required'),
            'subscription_details': free_mode_access.get('subscription_details'),
            'mode_name': 'Free Mode',
            'price': 0.0
        },
        'bronze_mode': {
            'has_access': bronze_mode_access['has_access'],
            'subscription_status': bronze_mode_access.get('subscription_status', 'no_subscription'),
            'subscription_details': bronze_mode_access.get('subscription_details'),
            'mode_name': 'Bronze Mode',
            'price': 5.0
        },
        'silver_mode': {
            'has_access': silver_mode_access['has_access'],
            'subscription_status': silver_mode_access.get('subscription_status', 'no_subscription'),
            'subscription_details': silver_mode_access.get('subscription_details'),
            'mode_name': 'Silver Mode',
            'price': 10.0
        }
    }
