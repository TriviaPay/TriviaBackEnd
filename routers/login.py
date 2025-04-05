from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import Dict, Optional
from pydantic import BaseModel

from db import get_db
from models import User, generate_account_id
from auth import verify_access_token, get_email_from_userinfo
import logging
from datetime import datetime

# Configure logging
logger = logging.getLogger(__name__)

# Define the Auth0TokenRequest model
class Auth0TokenRequest(BaseModel):
    """
    Model representing the token request from Auth0
    """
    access_token: str
    refresh_token: Optional[str] = None

class UserInfo(BaseModel):
    """
    Model representing user information in the response
    """
    username: str
    account_id: int
    badge: str
    badge_image_url: str

class LoginResponse(BaseModel):
    """
    Model representing the login response
    """
    access_token: str
    user_info: UserInfo

router = APIRouter(prefix="/login", tags=["Login"])

def get_badge_image_url(badge: str) -> str:
    """Get the image URL for a badge"""
    badge_urls = {
        "bronze": "https://drive.google.com/file/d/1Ih1bbxNUV9dgmEC8kCMgcomTYvifKlGZ/view?usp=sharing",
        "silver": "https://drive.google.com/file/d/1Ih1bbxNUV9dgmEC8kCMgcomTYvifKlGZ/view?usp=sharing",
        "gold": "https://drive.google.com/file/d/1Ih1bbxNUV9dgmEC8kCMgcomTYvifKlGZ/view?usp=sharing"
    }
    return badge_urls.get(badge.lower(), badge_urls["bronze"])

@router.post("/token", response_model=LoginResponse)
async def receive_auth0_tokens(
    tokens: Auth0TokenRequest, 
    db: Session = Depends(get_db)
):
    """
    Process Auth0 tokens and create/update user in the database
    """
    try:
        # Verify token and extract core information
        token_payload = verify_access_token(tokens.access_token)
        
        # Extract essential user details
        sub = token_payload.get('sub')
        email = token_payload.get('email')
        
        # Retrieve userinfo if email is missing
        if not email:
            userinfo = get_email_from_userinfo(tokens.access_token, return_full_info=True)
            if not userinfo:
                raise HTTPException(status_code=400, detail="Could not retrieve user email")
            email = userinfo.get('email')
        
        # Validate core information
        if not sub or not email:
            raise HTTPException(status_code=400, detail="Invalid user information")
        
        # Find existing user by email or sub
        user = db.query(User).filter(
            (User.email == email) | (User.sub == sub)
        ).first()
        
        # Prepare user data
        user_data = {
            'sub': sub,
            'email': email,
            'sign_up_date': datetime.utcnow(),
            'notification_on': True,
            'subscription_flag': False,
        }
        
        # Add profile picture if available
        userinfo = get_email_from_userinfo(tokens.access_token, return_full_info=True) or {}
        if userinfo.get('picture'):
            user_data['profile_pic_url'] = userinfo['picture']
        
        # Generate a default username from email
        base_username = email.split('@')[0]
        username = base_username
        
        # Update or create user
        if not user:
            # Generate a unique account_id and username
            max_attempts = 10
            for attempt in range(max_attempts):
                try:
                    # Generate a new account_id
                    user_data['account_id'] = generate_account_id()
                    
                    # Try with a numbered username if not first attempt
                    if attempt > 0:
                        username = f"{base_username}{attempt + 1}"
                    
                    user_data['username'] = username
                    
                    # Create new user
                    user = User(**user_data)
                    db.add(user)
                    
                    # Store refresh token
                    if tokens.refresh_token:
                        user.refresh_token = tokens.refresh_token
                    
                    # Commit changes
                    db.commit()
                    break
                except IntegrityError as e:
                    # Check if the error is due to duplicate username
                    if 'uq_users_username' in str(e):
                        # If we've tried max times with numbered usernames
                        if attempt == max_attempts - 1:
                            raise HTTPException(
                                status_code=400,
                                detail="Could not generate unique username. Please try again."
                            )
                        db.rollback()
                        continue
                    # If error is due to account_id, just retry
                    db.rollback()
            else:
                # If we've exhausted our attempts
                raise HTTPException(status_code=500, detail="Could not create user")
        else:
            # Update existing user
            for key, value in user_data.items():
                if value is not None:
                    setattr(user, key, value)
            
            # Store refresh token
            if tokens.refresh_token:
                user.refresh_token = tokens.refresh_token
            
            # Commit changes
            db.commit()
        
        # Return token information
        return LoginResponse(
            access_token=tokens.access_token,
            user_info=UserInfo(
                username=user.username or email.split('@')[0],
                account_id=user.account_id,
                badge=user.badge,
                badge_image_url=get_badge_image_url(user.badge)
            )
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token processing error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")