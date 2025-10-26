from fastapi import APIRouter, Depends, HTTPException, status, Request, Header
from sqlalchemy.orm import Session
from db import get_db
from models import User, Avatar, Frame, Badge
from descope.descope_client import DescopeClient
from config import DESCOPE_PROJECT_ID, DESCOPE_MANAGEMENT_KEY, DESCOPE_JWT_LEEWAY, STORE_PASSWORD_IN_DESCOPE, STORE_PASSWORD_IN_NEONDB
from auth import validate_descope_jwt
import logging
from datetime import datetime, timedelta, date as DateType
from collections import defaultdict
import time
from pydantic import BaseModel, Field
import re
from passlib.context import CryptContext
import os

router = APIRouter()
# Use management key for admin operations
mgmt_client = DescopeClient(project_id=DESCOPE_PROJECT_ID, management_key=DESCOPE_MANAGEMENT_KEY, jwt_validation_leeway=DESCOPE_JWT_LEEWAY)

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Simple in-memory rate limiter for endpoints
rate_limit_store = defaultdict(list)
RATE_LIMIT_WINDOW = 300  # 5 minutes
RATE_LIMIT_MAX_REQUESTS = 5  # 5 requests per window

def check_rate_limit(identifier: str) -> bool:
    """Check if the request is within rate limits"""
    now = time.time()
    # Clean old entries
    rate_limit_store[identifier] = [
        timestamp for timestamp in rate_limit_store[identifier] 
        if now - timestamp < RATE_LIMIT_WINDOW
    ]
    
    # Check if limit exceeded
    if len(rate_limit_store[identifier]) >= RATE_LIMIT_MAX_REQUESTS:
        return False
    
    # Add current request
    rate_limit_store[identifier].append(now)
    return True

class BindPasswordData(BaseModel):
    email: str = Field(..., description="User email (loginId)")
    password: str = Field(..., description="New password to bind")
    username: str = Field(..., description="Display name / username to set")
    country: str = Field(..., description="User country")
    date_of_birth: DateType = Field(..., description="User date of birth (YYYY-MM-DD)")


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
async def username_available(username: str, request: Request, db: Session = Depends(get_db)):
    """Return { available: true|false } indicating if a username is free."""
    try:
        # Rate limit per IP+username
        ip = request.client.host if request.client else "unknown"
        rl_key = f"ua:{ip}:{username.lower()}"
        if not check_rate_limit(rl_key):
            raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")
        
        exists = db.query(User).filter(User.username == username).first()
        return {"available": exists is None}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error checking username availability: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/email-available")
async def email_available(email: str, request: Request, db: Session = Depends(get_db)):
    """Return { available: true|false } indicating if an email is free."""
    try:
        # Rate limit per IP+email
        ip = request.client.host if request.client else "unknown"
        rl_key = f"ea:{ip}:{email.lower()}"
        if not check_rate_limit(rl_key):
            raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")
        
        # Check if email exists in the database
        exists = db.query(User).filter(User.email == email.lower()).first()
        return {"available": exists is None}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error checking email availability: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/bind-password")
async def bind_password(
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
        # Content-Type check
        content_type = request.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            raise HTTPException(status_code=415, detail="Unsupported Media Type. Use application/json")

        # Basic input validation
        _validate_password_strength(data.password)
        _validate_username(data.username)
        _validate_country(data.country)
        _validate_date_of_birth(data.date_of_birth)

        # Rate limiting check (IP + email)
        ip = request.client.host if request.client else "unknown"
        rate_identifier = f"{ip}:{data.email.lower()}"
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
        if session_email.lower() != data.email.lower():
            # If session email is a placeholder (contains @descope.local), allow any email
            if not session_email.endswith('@descope.local'):
                raise HTTPException(status_code=403, detail="User mismatch: session email does not match payload email")
            else:
                logging.info(f"Using provided email {data.email} instead of placeholder session email {session_email}")
        
        # Descope user management operations
        # Try to create or update user in Descope management system
        try:
            # First, try to load existing user from Descope
            try:
                user_details = mgmt_client.mgmt.user.load(user_id)
                logging.info(f"User exists in Descope, updating details: {user_id}")
                
                # User exists - update their details
                update_data = {
                    "email": data.email,
                    "displayName": data.username,
                    "name": data.username,
                    "customAttributes": {
                        "country": data.country,
                        "date_of_birth": str(data.date_of_birth)
                    }
                }
                
                mgmt_client.mgmt.user.update(
                    user_id=user_id,
                    update_data=update_data
                )
                
                # Set password in Descope if enabled
                if STORE_PASSWORD_IN_DESCOPE:
                    try:
                        mgmt_client.mgmt.user.set_password(data.email, data.password)
                        logging.info(f"Password set in Descope for user: {data.email}")
                    except Exception as e:
                        logging.error(f"Failed to set password in Descope: {e}")
                        raise HTTPException(
                            status_code=500,
                            detail=f"Failed to set password in authentication system: {str(e)}"
                        )
                
                logging.info(f"Successfully updated user in Descope: {user_id}")
                
            except Exception as load_error:
                # User doesn't exist in Descope - create them
                logging.info(f"User not found in Descope, creating new user: {user_id}")
                
                create_data = {
                    "login_id": data.email,
                    "email": data.email,
                    "display_name": data.username,
                    "name": data.username,
                    "custom_attributes": {
                        "country": data.country,
                        "date_of_birth": str(data.date_of_birth)
                    }
                }
                
                # Create user in Descope
                mgmt_client.mgmt.user.create(**create_data)
                
                # Set password for new user if enabled
                if STORE_PASSWORD_IN_DESCOPE:
                    try:
                        mgmt_client.mgmt.user.set_password(data.email, data.password)
                        logging.info(f"Password set in Descope for new user: {data.email}")
                    except Exception as e:
                        logging.error(f"Failed to set password in Descope for new user: {e}")
                        raise HTTPException(
                            status_code=500,
                            detail=f"Failed to set password in authentication system: {str(e)}"
                        )
                
                logging.info(f"Successfully created user in Descope: {user_id}")
                
        except Exception as descope_error:
            logging.error(f"Descope management operation failed: {descope_error}")
            raise HTTPException(
                status_code=500, 
                detail="Failed to sync user with authentication system. Please try again."
            )

        # Only proceed to NeonDB operations if Descope succeeds
        # Check if user already exists in local database
        existing_user = db.query(User).filter(User.email == data.email).first()
        if existing_user:
            # Update existing user
            existing_user.username = data.username
            existing_user.country = data.country
            existing_user.date_of_birth = data.date_of_birth
            existing_user.descope_user_id = user_id
            # Hash and store password in NeonDB if enabled
            if STORE_PASSWORD_IN_NEONDB:
                existing_user.password = pwd_context.hash(data.password)
            db.commit()
            logging.info(f"Updated existing user: {data.email}")
        else:
            # Check if username is taken by another user
            existing_username = db.query(User).filter(User.username == data.username).first()
            if existing_username:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "username_taken",
                        "message": f"Username '{data.username}' is already taken"
                    }
                )

            # Create new user
            new_user = User(
                descope_user_id=user_id,
                email=data.email,
                username=data.username,
                display_name=data.username,
                country=data.country,
                date_of_birth=data.date_of_birth,
                notification_on=True,
                gems=0,
                streaks=0,
                lifeline_changes_remaining=3,
                referral_count=0,
                is_referred=False,
                is_admin=False,
                username_updated=False,
                subscription_flag=False,
                sign_up_date=datetime.utcnow(),
                wallet_balance=0.0,
                total_spent=0.0,
                # Hash and store password in NeonDB if enabled
                password=pwd_context.hash(data.password) if STORE_PASSWORD_IN_NEONDB else None,
            )
            db.add(new_user)
            db.commit()
            logging.info(f"Created new user: {data.email}")
        
        return {"success": True, "message": "Password and profile bound successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error in bind_password: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/test-descope-auth")
async def test_descope_auth(request: Request):
    """
    Test endpoint to verify Descope JWT authentication.
    
    This endpoint requires a valid Descope session JWT in the Authorization header.
    Returns the user information from the validated session.
    """
    try:
        # Extract JWT from Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        token = auth_header.split(" ", 1)[1]
        
        # Validate JWT and get user info
        user_info = validate_descope_jwt(token)
        
        return {
            "message": "Authentication successful",
            "user": user_info
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error in test_descope_auth: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

class DevMintRequest(BaseModel):
    identifier: str = Field(..., description="Email or username")

@router.post("/dev/mint-session")
async def dev_mint_session(
    request: Request,
    data: DevMintRequest,
    x_dev_secret: str = Header(None, alias="X-Dev-Secret", description="Dev-only secret to authorize minting"),
    db: Session = Depends(get_db)
):
    """
    TEMPORARY: Mint a Descope session token for a user by email or username.
    - Dev-only: requires ENVIRONMENT=development
    - Requires header X-Dev-Secret matching DEV_ADMIN_SECRET env
    Body: { "identifier": "email-or-username" }
    """
    import os
    if os.getenv("ENVIRONMENT", "development") != "development":
        raise HTTPException(status_code=403, detail="Not available in this environment")
    dev_secret = os.getenv("DEV_ADMIN_SECRET")
    if not dev_secret:
        raise HTTPException(status_code=500, detail="DEV_ADMIN_SECRET not configured")
    if x_dev_secret != dev_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    identifier = (data.identifier or "").strip()
    if not identifier:
        raise HTTPException(status_code=400, detail="Missing identifier")

    # Resolve user by email or username
    user = db.query(User).filter(User.email == identifier).first()
    if not user:
        user = db.query(User).filter(User.username == identifier).first()
    if not user or not user.descope_user_id:
        raise HTTPException(status_code=404, detail="User not found or missing descope_user_id")

    # Try management impersonation methods
    try:
        # Attempt 1: impersonate
        resp = mgmt_client.mgmt.user.impersonate(user.descope_user_id)
        session_jwt = resp.get('sessionJwt') or resp.get('jwt')
        if session_jwt:
            return {"session_jwt": session_jwt, "user_id": user.descope_user_id}
    except Exception as e1:
        logging.debug(f"impersonate failed: {e1}")
    try:
        # Attempt 2: login_as
        resp2 = mgmt_client.mgmt.user.login_as(user.descope_user_id)
        session_jwt2 = resp2.get('sessionJwt') or resp2.get('jwt')
        if session_jwt2:
            return {"session_jwt": session_jwt2, "user_id": user.descope_user_id}
    except Exception as e2:
        logging.debug(f"login_as failed: {e2}")

    logging.error("Descope management client could not mint a session JWT in this environment")
    raise HTTPException(status_code=501, detail="Minting session is not supported by the current Descope SDK/mode")

class DevOtpInitRequest(BaseModel):
    email: str = Field(..., description="Email to send OTP to")

class DevOtpVerifyRequest(BaseModel):
    email: str = Field(..., description="Email that received the OTP")
    code: str = Field(..., description="OTP code from email")

@router.post("/dev/otp/send")
async def dev_send_otp(
    request: Request,
    data: DevOtpInitRequest,
    x_dev_secret: str = Header(None, alias="X-Dev-Secret", description="Dev-only secret to authorize"),
):
    """
    Dev-only: Send OTP to email address for testing
    """
    if os.getenv("ENVIRONMENT", "development") != "development":
        raise HTTPException(status_code=403, detail="Not available in this environment")
    dev_secret = os.getenv("DEV_ADMIN_SECRET")
    if not dev_secret or x_dev_secret != dev_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    email = data.email.strip()
    
    try:
        from descope import DescopeClient
        from descope.common import DeliveryMethod
        
        project_id = os.getenv("DESCOPE_PROJECT_ID", DESCOPE_PROJECT_ID)
        if not project_id:
            raise HTTPException(status_code=500, detail="Descope project ID not configured")
            
        client = DescopeClient(project_id=project_id, jwt_validation_leeway=DESCOPE_JWT_LEEWAY)
        
        # Try sign-up-or-in (works for both new and existing users)
        response = client.otp.sign_up_or_in(DeliveryMethod.EMAIL, email)
        
        return {
            "email": email,
            "message": "OTP sent successfully! Check your email.",
            "next_step": "Use the /dev/otp/verify endpoint with the OTP code from your email"
        }
        
    except ImportError:
        raise HTTPException(status_code=500, detail="Descope Python SDK not installed")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to send OTP: {str(e)}")

@router.post("/dev/otp/verify")
async def dev_verify_otp(
    request: Request,
    data: DevOtpVerifyRequest,
    x_dev_secret: str = Header(None, alias="X-Dev-Secret", description="Dev-only secret to authorize"),
):
    """
    Dev-only: Verify OTP and get JWT token for testing
    """
    if os.getenv("ENVIRONMENT", "development") != "development":
        raise HTTPException(status_code=403, detail="Not available in this environment")
    dev_secret = os.getenv("DEV_ADMIN_SECRET")
    if not dev_secret or x_dev_secret != dev_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    email = data.email.strip()
    code = data.code.strip()
    
    try:
        from descope import DescopeClient
        from descope.common import DeliveryMethod
        
        project_id = os.getenv("DESCOPE_PROJECT_ID", DESCOPE_PROJECT_ID)
        if not project_id:
            raise HTTPException(status_code=500, detail="Descope project ID not configured")
            
        client = DescopeClient(project_id=project_id, jwt_validation_leeway=DESCOPE_JWT_LEEWAY)
        
        # Verify the OTP code using the correct Python SDK pattern
        response = client.otp.verify_code(DeliveryMethod.EMAIL, email, code)
        
        # Extract session JWT from response - try multiple possible locations
        session_jwt = None
        logging.debug(f"OTP verify response type: {type(response)}")
        logging.debug(f"OTP verify response: {response}")
        
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
                              response.get("token"))
        elif hasattr(response, '__dict__'):
            # If it's an object, try to access its attributes
            resp_dict = response.__dict__
            session_jwt = (resp_dict.get("sessionJwt") or 
                          resp_dict.get("session_jwt") or 
                          resp_dict.get("jwt") or
                          resp_dict.get("token"))
        
        # If still no JWT, try to convert response to string and see if it's the JWT itself
        if not session_jwt:
            resp_str = str(response)
            if len(resp_str) > 50 and resp_str.startswith('eyJ'):  # JWT tokens start with eyJ
                session_jwt = resp_str
        
        if not session_jwt:
            raise HTTPException(status_code=502, detail=f"No session JWT found in response. Response type: {type(response)}, Response: {response}")
            
        return {
            "email": email,
            "session_jwt": session_jwt
        }
        
    except ImportError:
        raise HTTPException(status_code=500, detail="Descope Python SDK not installed")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to verify OTP: {str(e)}")