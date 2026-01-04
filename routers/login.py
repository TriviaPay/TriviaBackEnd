from fastapi import APIRouter, Depends, HTTPException, status, Request, Header
from sqlalchemy.orm import Session
from sqlalchemy import func
from db import get_db
from models import User, Avatar, Frame, TriviaModeConfig
import logging
from descope.descope_client import DescopeClient
from config import DESCOPE_PROJECT_ID, DESCOPE_MANAGEMENT_KEY, DESCOPE_JWT_LEEWAY, STORE_PASSWORD_IN_DESCOPE, STORE_PASSWORD_IN_NEONDB, AWS_DEFAULT_PROFILE_PIC_BASE_URL
from auth import validate_descope_jwt
from datetime import datetime, timedelta, date as DateType
from collections import OrderedDict, deque
from typing import Optional
import time
from pydantic import BaseModel, Field
import re
from passlib.context import CryptContext
import os
from utils.referrals import get_unique_referral_code
import threading
import redis
from redis.exceptions import ConnectionError, TimeoutError, RedisError

router = APIRouter()
# Use management key for admin operations
mgmt_client = DescopeClient(project_id=DESCOPE_PROJECT_ID, management_key=DESCOPE_MANAGEMENT_KEY, jwt_validation_leeway=DESCOPE_JWT_LEEWAY)

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Simple rate limiter for endpoints (Redis if available, fallback to in-memory)
rate_limit_store = OrderedDict()
rate_limit_lock = threading.Lock()
rate_limit_redis = None
rate_limit_redis_unavailable = False
rate_limit_redis_last_retry = 0.0
RATE_LIMIT_REDIS_RETRY_SECONDS = 60
RATE_LIMIT_WINDOW = 300  # 5 minutes
RATE_LIMIT_MAX_REQUESTS = 5  # 5 requests per window
RATE_LIMIT_MAX_KEYS = 10000

def check_rate_limit(identifier: str) -> bool:
    """Check if the request is within rate limits"""
    now = time.time()
    # Try Redis-backed rate limiting first
    global rate_limit_redis, rate_limit_redis_unavailable, rate_limit_redis_last_retry
    if rate_limit_redis_unavailable and (now - rate_limit_redis_last_retry) < RATE_LIMIT_REDIS_RETRY_SECONDS:
        rate_limit_redis = None
    elif rate_limit_redis_unavailable:
        rate_limit_redis_unavailable = False
        rate_limit_redis = None
    if rate_limit_redis is None and not rate_limit_redis_unavailable:
        try:
            from config import REDIS_URL
            rate_limit_redis = redis.Redis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2
            )
            rate_limit_redis.ping()
            rate_limit_redis_unavailable = False
        except Exception:
            rate_limit_redis_unavailable = True
            rate_limit_redis_last_retry = now
            rate_limit_redis = None

    if rate_limit_redis:
        try:
            key = f"rl:login:{identifier}"
            pipe = rate_limit_redis.pipeline()
            pipe.incr(key, 1)
            pipe.expire(key, RATE_LIMIT_WINDOW)
            count, _ = pipe.execute()
            return int(count) <= RATE_LIMIT_MAX_REQUESTS
        except (ConnectionError, TimeoutError, RedisError, OSError):
            rate_limit_redis_unavailable = True
            rate_limit_redis_last_retry = now
            rate_limit_redis = None

    # Fallback to in-memory LRU buckets
    with rate_limit_lock:
        bucket = rate_limit_store.get(identifier)
        if bucket is None:
            bucket = deque()
            rate_limit_store[identifier] = bucket
        else:
            rate_limit_store.move_to_end(identifier)

        while bucket and now - bucket[0] >= RATE_LIMIT_WINDOW:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
            return False

        bucket.append(now)
        if len(rate_limit_store) > RATE_LIMIT_MAX_KEYS:
            rate_limit_store.popitem(last=False)

    return True

def get_default_profile_pic_url(username: str) -> Optional[str]:
    """
    Generate default profile picture URL based on first letter of username.
    
    Uses AWS_DEFAULT_PROFILE_PIC_BASE_URL environment variable.
    Format: {BASE_URL}{first_letter}.png
    Example: https://triviapics.s3.us-east-2.amazonaws.com/default_profile_pics/a.png
    
    Args:
        username: The username to generate profile pic for
        
    Returns:
        Profile picture URL string or None if base URL not configured
    """
    if not AWS_DEFAULT_PROFILE_PIC_BASE_URL:
        logging.warning("AWS_DEFAULT_PROFILE_PIC_BASE_URL not configured, skipping default profile pic")
        return None
    
    if not username:
        return None
    
    # Get the first letter of the username, convert to lowercase
    first_letter = username[0].lower()
    
    # If not a letter, default to 'a'
    if not first_letter.isalpha():
        first_letter = 'a'
    
    # Construct URL: base_url + letter + .png
    # Ensure base_url ends with / to avoid double slashes
    base_url = AWS_DEFAULT_PROFILE_PIC_BASE_URL.rstrip('/')
    profile_pic_url = f"{base_url}/{first_letter}.png"
    
    return profile_pic_url

class BindPasswordData(BaseModel):
    email: str = Field(..., description="User email (loginId)")
    password: str = Field(..., description="New password to bind")
    username: str = Field(..., description="Display name / username to set")
    country: str = Field(..., description="User country")
    date_of_birth: DateType = Field(..., description="User date of birth (YYYY-MM-DD)")
    referral_code: Optional[str] = Field(None, description="Optional referral code")


def _validate_password_strength(password: str):
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long")
    if not re.search(r"[A-Za-z]", password):
        raise HTTPException(status_code=400, detail="Password must contain at least one letter")
    if not re.search(r"\d", password):
        raise HTTPException(status_code=400, detail="Password must contain at least one number")


def _validate_username(username: str):
    if len(username) < 3 or len(username) > 30:
        raise HTTPException(status_code=400, detail="Username must be between 3 and 30 characters")
    if not re.match(r"^[A-Za-z0-9_.-]+$", username):
        raise HTTPException(status_code=400, detail="Username may contain letters, numbers, and . _ - only")


def _validate_country(country: str):
    if not country or len(country.strip()) < 2:
        raise HTTPException(status_code=400, detail="Country is required")
    if len(country) > 64:
        raise HTTPException(status_code=400, detail="Country is too long")


def _validate_date_of_birth(dob: DateType):
    today = datetime.utcnow().date()
    if dob >= today:
        raise HTTPException(status_code=400, detail="Date of birth must be in the past")
    # Basic age check (13+)
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    if age < 13:
        raise HTTPException(status_code=400, detail="You must be at least 13 years old")


@router.get("/username-available")
def username_available(username: str, request: Request, db: Session = Depends(get_db)):
    """Return { available: true|false } indicating if a username is free."""
    try:
        # Rate limit per IP+username
        ip = request.client.host if request.client else "unknown"
        username_norm = username.strip()
        rl_key = f"ua:{ip}:{username_norm.lower()}"
        if not check_rate_limit(rl_key):
            raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")
        
        exists = db.query(User).filter(func.lower(User.username) == username_norm.lower()).first()
        return {"available": exists is None}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error checking username availability: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/email-available")
def email_available(email: str, request: Request, db: Session = Depends(get_db)):
    """Return { available: true|false } indicating if an email is free."""
    try:
        # Rate limit per IP+email
        ip = request.client.host if request.client else "unknown"
        email_norm = email.strip().lower()
        rl_key = f"ea:{ip}:{email_norm}"
        if not check_rate_limit(rl_key):
            raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")
        
        # Check if email exists in the database
        exists = db.query(User).filter(func.lower(User.email) == email_norm).first()
        return {"available": exists is None}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error checking email availability: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/bind-password")
def bind_password(
    request: Request,
    data: BindPasswordData,
    db: Session = Depends(get_db)
):
    """
    Bind a password and username to a Descope user.
    
    This endpoint requires a valid Descope session JWT in the Authorization header.
    It validates the JWT, matches the email, updates the password and username via Descope,
    and syncs the username/displayName/country/date_of_birth to the local database.
    
    Rate limited to 5 requests per 5 minutes per IP+email.
    """
    try:
        email = data.email.strip().lower()
        username = data.username.strip()
        country = data.country.strip()

        # Log the bind-password request (never log raw passwords)
        logging.info(
            f"[BIND_PASSWORD] 📝 Bind password request received - "
            f"LoginId: '{email}', "
            f"Username: '{username}', "
            f"Country: '{country}', "
            f"ReferralCode: '{data.referral_code if data.referral_code else 'None'}', "
            f"PasswordLength: {len(data.password)}, "
            f"Timestamp: '{datetime.utcnow().isoformat()}'"
        )
        
        # Content-Type check
        content_type = request.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            raise HTTPException(status_code=415, detail="Unsupported Media Type. Use application/json")

        # Basic input validation
        _validate_password_strength(data.password)
        _validate_username(username)
        _validate_country(country)
        _validate_date_of_birth(data.date_of_birth)

        # Rate limiting check (IP + email)
        ip = request.client.host if request.client else "unknown"
        rate_identifier = f"{ip}:{email}"
        if not check_rate_limit(rate_identifier):
            raise HTTPException(
                status_code=429, 
                detail="Too many requests. Please try again later."
            )
        
        # Extract JWT from Authorization header
        auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        token = auth_header.split(" ", 1)[1].strip()
        
        # Validate JWT and get user info
        user_info = validate_descope_jwt(token)

        # Derive userId and session email from claims
        user_id = user_info.get("userId") or user_info.get("sub")
        session_login_ids = user_info.get("loginIds") or []
        session_email = None
        if isinstance(session_login_ids, list) and len(session_login_ids) > 0:
            session_email = session_login_ids[0]
        # Some sessions may include email directly
        if not session_email:
            session_email = user_info.get("email")

        if not user_id or not session_email:
            raise HTTPException(status_code=400, detail="Invalid user information from session")

        # Enforce that the session email matches the requested email
        # Special handling for placeholder emails created during JWT validation
        if session_email.lower() != email:
            # If session email is a placeholder (contains @descope.local), allow any email
            if not session_email.endswith('@descope.local'):
                raise HTTPException(status_code=403, detail="User mismatch: session email does not match payload email")
            else:
                logging.info(f"Using provided email {email} instead of placeholder session email {session_email}")
        
        # Descope user management operations
        # Try to create or update user in Descope management system
        try:
            # First, try to load existing user from Descope
            try:
                user_details = mgmt_client.mgmt.user.load(user_id)
                logging.info(f"User exists in Descope, updating details: {user_id}")
                
                # User exists - update their details
                update_data = {
                    "email": email,
                    "display_name": username,
                    "custom_attributes": {
                        "country": country,
                        "date_of_birth": str(data.date_of_birth)
                    }
                }
                
                mgmt_client.mgmt.user.update(
                    login_id=email,
                    **update_data
                )
                
                # Set password in Descope if enabled
                if STORE_PASSWORD_IN_DESCOPE:
                    try:
                        # Log password binding attempt
                        logging.info(
                            f"[PASSWORD_BINDING] Attempting to set password for user - "
                            f"LoginId: '{email}', "
                            f"UserId: '{user_id}', "
                            f"HasPassword: {user_details.get('password', False) if 'user_details' in locals() else 'Unknown'}, "
                            f"PasswordLength: {len(data.password)}"
                        )
                        
                        # Use set_active_password to ensure password is active (sign-in ready)
                        mgmt_client.mgmt.user.set_active_password(email, data.password)
                        
                        # Log successful password binding
                        logging.info(
                            f"[PASSWORD_BINDING] ✅ Password successfully set in Descope (ACTIVE) - "
                            f"LoginId: '{email}', "
                            f"UserId: '{user_id}', "
                            f"Method: 'set_active_password', "
                            f"Timestamp: '{datetime.utcnow().isoformat()}'"
                        )
                        
                        # Verify password was set as active
                        user_check = mgmt_client.mgmt.user.load(email)
                        has_active_password = user_check.get('user', {}).get('password', False) if isinstance(user_check, dict) else False
                        logging.info(
                            f"[PASSWORD_BINDING] Password verification - "
                            f"LoginId: '{email}', "
                            f"ActivePasswordSet: {has_active_password}"
                        )
                        if not has_active_password:
                            logging.error(
                                "[PASSWORD_BINDING] ⚠️ Password was set but not activated! User may not be able to sign in."
                            )
                    except Exception:
                        logging.error(
                            f"[PASSWORD_BINDING] ❌ Failed to set password in Descope - "
                            f"LoginId: '{email}', "
                            f"UserId: '{user_id}'",
                            exc_info=True,
                        )
                        raise HTTPException(
                            status_code=500,
                            detail="Failed to set password in authentication system"
                        )
                
                logging.info(f"Successfully updated user in Descope: {user_id}")
                
            except Exception as load_error:
                # Check if it's a 404 (user not found) or other error
                if "not found" in str(load_error).lower() or "404" in str(load_error):
                    # User doesn't exist in Descope - create them
                    logging.info(f"User not found in Descope, creating new user: {user_id}")
                    
                    create_data = {
                        "login_id": email,
                        "email": email,
                        "display_name": username,
                        "custom_attributes": {
                            "country": country,
                            "date_of_birth": str(data.date_of_birth)
                        }
                    }
                    
                    # Create user in Descope
                    mgmt_client.mgmt.user.create(**create_data)
                    
                    # Set password for new user if enabled
                    if STORE_PASSWORD_IN_DESCOPE:
                        try:
                            # Log password binding attempt for new user
                            logging.info(
                                f"[PASSWORD_BINDING] Attempting to set password for NEW user - "
                                f"LoginId: '{email}', "
                                f"UserId: '{user_id}', "
                                f"PasswordLength: {len(data.password)}"
                            )
                            
                            # Use set_active_password to ensure password is active (sign-in ready)
                            mgmt_client.mgmt.user.set_active_password(email, data.password)
                            
                            # Log successful password binding for new user
                            logging.info(
                                f"[PASSWORD_BINDING] ✅ Password successfully set in Descope for NEW user (ACTIVE) - "
                                f"LoginId: '{email}', "
                                f"UserId: '{user_id}', "
                                f"Method: 'set_active_password', "
                                f"Timestamp: '{datetime.utcnow().isoformat()}'"
                            )
                            
                            # Verify password was set as active for new user
                            user_check = mgmt_client.mgmt.user.load(email)
                            has_active_password = user_check.get('user', {}).get('password', False) if isinstance(user_check, dict) else False
                            logging.info(
                                f"[PASSWORD_BINDING] Password verification for NEW user - "
                                f"LoginId: '{email}', "
                                f"ActivePasswordSet: {has_active_password}"
                            )
                            if not has_active_password:
                                logging.error(
                                    "[PASSWORD_BINDING] ⚠️ Password was set but not activated for new user! User may not be able to sign in."
                                )
                        except Exception:
                            logging.error(
                                f"[PASSWORD_BINDING] ❌ Failed to set password in Descope for NEW user - "
                                f"LoginId: '{email}', "
                                f"UserId: '{user_id}'",
                                exc_info=True,
                            )
                            raise HTTPException(
                                status_code=500,
                                detail="Failed to set password in authentication system"
                            )
                    
                    logging.info(f"Successfully created user in Descope: {user_id}")
                else:
                    # Re-raise other errors (like parameter issues)
                    logging.error(f"Descope user operation failed: {load_error}")
                    raise HTTPException(
                        status_code=500, 
                        detail="Failed to sync user with authentication system. Please try again."
                    )
                
        except Exception as descope_error:
            logging.error(f"Descope management operation failed: {descope_error}")
            raise HTTPException(
                status_code=500, 
                detail="Failed to sync user with authentication system. Please try again."
            )

        # Only proceed to NeonDB operations if Descope succeeds
        # Check if user already exists in local database
        existing_user = db.query(User).filter(func.lower(User.email) == email).first()
        if existing_user:
            # Update existing user
            existing_user.username = username
            existing_user.country = country
            existing_user.date_of_birth = data.date_of_birth
            existing_user.descope_user_id = user_id
            
            # Set default profile pic URL if user doesn't have one
            if not existing_user.profile_pic_url:
                profile_pic_url = get_default_profile_pic_url(username)
                if profile_pic_url:
                    existing_user.profile_pic_url = profile_pic_url
                    logging.info(f"Set default profile pic for existing user: {profile_pic_url}")
            
            # Hash and store password in NeonDB if enabled
            if STORE_PASSWORD_IN_NEONDB:
                existing_user.password = pwd_context.hash(data.password)
            
            # Process referral code if provided (only if user doesn't already have one)
            if data.referral_code and not existing_user.referred_by:
                try:
                    referrer = db.query(User).filter(
                        User.referral_code == data.referral_code
                    ).with_for_update().first()
                    if not referrer:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invalid referral code '{data.referral_code}'. Please check and try again."
                        )
                    
                    if referrer.account_id == existing_user.account_id:
                        raise HTTPException(
                            status_code=400,
                            detail="You cannot use your own referral code."
                        )
                    
                    # Update referrer's count and mark current user as referred
                    referrer.referral_count = (referrer.referral_count or 0) + 1
                    existing_user.referred_by = data.referral_code
                    logging.info(
                        f"[REFERRAL] Successfully applied referral code: {data.referral_code} from user {referrer.username} to {email}"
                    )
                except HTTPException:
                    db.rollback()
                    raise
                except Exception as e:
                    logging.error(f"Error processing referral code: {str(e)}")
                    db.rollback()
                    raise HTTPException(
                        status_code=500,
                        detail="Error processing referral code. Please try again."
                    )
            
            db.commit()
            logging.info(
                f"[LOCAL_DB] Updated existing user in local database - "
                f"Email: '{email}', "
                f"DescopeUserId: '{user_id}', "
                f"LocalPasswordStored: {STORE_PASSWORD_IN_NEONDB}, "
                f"ReferralCode: {data.referral_code if data.referral_code else 'None'}"
            )
        else:
            # Check if username is taken by another user
            existing_username = db.query(User).filter(func.lower(User.username) == username.lower()).first()
            if existing_username:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "username_taken",
                        "message": f"Username '{username}' is already taken"
                    }
                )

            # Process referral code if provided (for new users)
            referred_by_code = None
            if data.referral_code:
                try:
                    referrer = db.query(User).filter(
                        User.referral_code == data.referral_code
                    ).with_for_update().first()
                    if not referrer:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invalid referral code '{data.referral_code}'. Please check and try again."
                        )
                    
                    # For new users, we can't check if they're using their own code since they don't exist yet
                    # But we'll set referred_by and increment referrer's count
                    referrer.referral_count = (referrer.referral_count or 0) + 1
                    referred_by_code = data.referral_code
                    logging.info(
                        f"[REFERRAL] New user will be referred by: {data.referral_code} from user {referrer.username}"
                    )
                except HTTPException:
                    db.rollback()
                    raise
                except Exception as e:
                    logging.error(f"Error processing referral code: {str(e)}")
                    db.rollback()
                    raise HTTPException(
                        status_code=500,
                        detail="Error processing referral code. Please try again."
                    )

            # Generate default profile pic URL based on first letter of username
            profile_pic_url = get_default_profile_pic_url(username)
            if profile_pic_url:
                logging.info(f"Generated default profile pic URL for new user: {profile_pic_url}")
            
            # Create new user
            new_user = User(
                descope_user_id=user_id,
                email=email,
                username=username,
                country=country,
                date_of_birth=data.date_of_birth,
                profile_pic_url=profile_pic_url,  # Set default profile pic based on first letter
                notification_on=True,
                gems=0,
                referral_count=0,
                referral_code=get_unique_referral_code(db),
                is_admin=False,
                username_updated=False,
                subscription_flag=False,
                sign_up_date=datetime.utcnow(),
                wallet_balance=0.0,
                total_spent=0.0,
                referred_by=referred_by_code,
                # Hash and store password in NeonDB if enabled
                password=pwd_context.hash(data.password) if STORE_PASSWORD_IN_NEONDB else None,
            )
            db.add(new_user)
            db.commit()
            logging.info(
                f"[LOCAL_DB] Created new user in local database - "
                f"Email: '{email}', "
                f"DescopeUserId: '{user_id}', "
                f"LocalPasswordStored: {STORE_PASSWORD_IN_NEONDB}, "
                f"ReferralCode: {data.referral_code if data.referral_code else 'None'}"
            )
        
        # Final success log
        logging.info(
            f"[BIND_PASSWORD] ✅ Successfully completed password binding - "
            f"LoginId: '{email}', "
            f"UserId: '{user_id}', "
            f"Username: '{username}', "
            f"DescopePasswordSet: {STORE_PASSWORD_IN_DESCOPE}, "
            f"LocalPasswordStored: {STORE_PASSWORD_IN_NEONDB}, "
            f"Timestamp: '{datetime.utcnow().isoformat()}'"
        )
        
        return {"success": True, "message": "Password and profile bound successfully"}
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logging.error(
            f"[BIND_PASSWORD] ❌ Fatal error in bind_password - "
            f"LoginId: '{email if 'email' in locals() else 'Unknown'}', "
            f"Error: {str(e)}, "
            f"ErrorType: {type(e).__name__}"
        )
        raise HTTPException(status_code=500, detail="Internal server error")

class DevSignInRequest(BaseModel):
    email: str = Field(..., description="Email address (loginId)", example="triviapay3@gmail.com")
    password: str = Field(..., description="User password", example="Trivia@1")


@router.post("/dev/sign-in")
def dev_sign_in(
    request: Request,
    data: DevSignInRequest,
    x_dev_secret: str = Header(None, alias="X-Dev-Secret", description="Dev-only secret to authorize", example="TriviaPay"),
):
    """
    Dev-only: Sign in with email and password, returns access token (session JWT)
    
    This endpoint uses Descope's password authentication API to authenticate users
    and returns the session JWT token for use in subsequent requests.
    """
    if os.getenv("ENVIRONMENT", "development") != "development":
        raise HTTPException(status_code=403, detail="Not available in this environment")
    dev_secret = os.getenv("DEV_ADMIN_SECRET")
    if not dev_secret or x_dev_secret != dev_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    email = data.email.strip()
    password = data.password
    
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")
    if not password:
        raise HTTPException(status_code=400, detail="Password is required")
    
    try:
        from descope import DescopeClient
        
        project_id = os.getenv("DESCOPE_PROJECT_ID", DESCOPE_PROJECT_ID)
        if not project_id:
            raise HTTPException(status_code=500, detail="Descope project ID not configured")
            
        client = DescopeClient(project_id=project_id, jwt_validation_leeway=DESCOPE_JWT_LEEWAY)
        
        # Sign in with email and password using Descope's password authentication
        # This will work for both sign-up and sign-in if the user exists
        try:
            response = client.password.sign_in(email, password)
        except AttributeError:
            # Fallback: try alternative method names
            try:
                response = client.auth.sign_in(email, password)
            except AttributeError:
                # Try with login_id parameter
                try:
                    response = client.password.sign_in(login_id=email, password=password)
                except Exception as e:
                    raise HTTPException(
                        status_code=500, 
                        detail=f"Descope SDK method not found. Available methods: {dir(client)}. Error: {str(e)}"
                    )
        
        # Extract session JWT from response
        session_jwt = None
        logging.debug(f"Sign-in response type: {type(response)}")
        logging.debug(f"Sign-in response: {response}")
        
        # Try different ways to extract the JWT
        if hasattr(response, 'session_jwt'):
            session_jwt = response.session_jwt
        elif hasattr(response, 'sessionJwt'):
            session_jwt = response.sessionJwt
        elif isinstance(response, dict):
            # Check for nested sessionToken structure first
            if "sessionToken" in response and isinstance(response["sessionToken"], dict):
                session_jwt = response["sessionToken"].get("jwt")
            
            # Fallback to direct keys
            if not session_jwt:
                session_jwt = (response.get("sessionJwt") or 
                              response.get("session_jwt") or 
                              response.get("jwt") or
                              response.get("token") or
                              response.get("session_token"))
        elif hasattr(response, '__dict__'):
            # If it's an object, try to access its attributes
            resp_dict = response.__dict__
            session_jwt = (resp_dict.get("sessionJwt") or 
                          resp_dict.get("session_jwt") or 
                          resp_dict.get("jwt") or
                          resp_dict.get("token") or
                          resp_dict.get("session_token"))
        
        # If still no JWT, try to convert response to string and see if it's the JWT itself
        if not session_jwt:
            resp_str = str(response)
            if len(resp_str) > 50 and resp_str.startswith('eyJ'):  # JWT tokens start with eyJ
                session_jwt = resp_str
        
        if not session_jwt:
            logging.error(f"No session JWT found in response. Response type: {type(response)}, Response: {response}")
            raise HTTPException(
                status_code=502, 
                detail=f"No session JWT found in response. Response type: {type(response)}"
            )
        
        logging.info(f"[DEV_SIGN_IN] ✅ Successfully signed in user: {email}")
        
        return {
            "access_token": session_jwt
        }
        
    except ImportError:
        raise HTTPException(status_code=500, detail="Descope Python SDK not installed")
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        logging.error(f"[DEV_SIGN_IN] ❌ Failed to sign in user {email}: {error_msg}")
        
        # Check for specific error types
        if "invalid" in error_msg.lower() or "incorrect" in error_msg.lower() or "wrong" in error_msg.lower():
            raise HTTPException(status_code=401, detail="Invalid email or password")
        elif "not found" in error_msg.lower() or "does not exist" in error_msg.lower():
            raise HTTPException(status_code=404, detail="User not found")
        elif "locked" in error_msg.lower() or "blocked" in error_msg.lower():
            raise HTTPException(status_code=403, detail="Account is locked or blocked")
        else:
            raise HTTPException(status_code=502, detail=f"Failed to sign in: {error_msg}")

class ReferralCheck(BaseModel):
    referral_code: str = Field(..., description="Referral code to validate")

@router.post("/validate-referral")
def validate_referral_code(
    referral_data: ReferralCheck,
    db: Session = Depends(get_db)
):
    """
    Validate if a referral code is valid.
    No authentication required.
    """
    try:
        # Find the referrer
        referrer = db.query(User).filter(User.referral_code == referral_data.referral_code).first()
        
        if not referrer:
            return {
                "status": "error",
                "message": f"Invalid referral code '{referral_data.referral_code}'. Please check and try again.",
                "code": "INVALID_REFERRAL_CODE",
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

@router.get("/countries")
def get_countries(
    db: Session = Depends(get_db)
):
    """
    Get list of countries.
    No authentication required.
    Returns a simple country list.
    """
    try:
        # Simple country list
        countries = [
            "United States", "Canada", "United Kingdom", "Australia", "India",
            "Germany", "France", "Japan", "Brazil", "Mexico", "China", "Spain",
            "Italy", "Russia", "South Korea", "Singapore", "New Zealand",
            "South Africa", "Nigeria", "Kenya", "Egypt", "Saudi Arabia",
            "United Arab Emirates", "Pakistan", "Bangladesh", "Malaysia",
            "Indonesia", "Philippines", "Vietnam", "Thailand"
        ]
        
        return {
            "status": "success",
            "countries": sorted(countries),
            "country_codes": []  # Empty array since country_codes table is removed
        }
    except Exception as e:
        logging.error(f"Error fetching countries: {str(e)}")
        return {
            "status": "error",
            "message": f"An unexpected error occurred: {str(e)}",
            "code": "UNEXPECTED_ERROR"
        }
