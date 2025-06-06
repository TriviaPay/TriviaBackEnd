from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import Dict, Optional
from pydantic import BaseModel
from fastapi.security import OAuth2PasswordBearer

from db import get_db
from models import User, generate_account_id, Avatar, Frame, Badge
from auth import verify_access_token, get_email_from_userinfo
import logging
from datetime import datetime
from utils import get_letter_profile_pic  # Import the new utility function
import random
import string

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
    badge_id: Optional[str] = None
    badge_name: Optional[str] = None
    badge_image_url: Optional[str] = None
    is_existing_user: bool
    avatar_url: Optional[str] = None
    frame_url: Optional[str] = None

class LoginResponse(BaseModel):
    """
    Model representing the login response
    """
    access_token: str
    user_info: UserInfo

router = APIRouter(prefix="/login", tags=["Login"])

@router.post("/token", response_model=LoginResponse)
async def receive_auth0_tokens(
    tokens: Auth0TokenRequest, 
    db: Session = Depends(get_db)
):
    """
    Receive and process tokens from Auth0, validating the access token
    and returning user information.
    """
    logger = logging.getLogger(__name__)
    
    try:
        # Verify token with Auth0
        claims = verify_access_token(tokens.access_token)
        
        if not claims:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid access token"
            )
        
        # Extract user info
        sub = claims.get('sub')
        email = claims.get('email')
        
        # Safety check
        if not sub or not email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token claims - missing sub or email"
            )
        
        # Find user in database
        user = db.query(User).filter(
            (User.email == email) | (User.sub == sub)
        ).first()
        
        # Check if user is existing
        is_existing_user = user is not None
        
        # Prepare user data
        user_data = {
            'sub': sub,
            'email': email,
            'sign_up_date': datetime.utcnow(),
            'notification_on': True,
            'subscription_flag': False,
            'badge_id': None, # No default badge
            'badge_image_url': None # No default badge image
        }
        
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
                    
                    # Set profile picture URL based on first letter of username
                    user_data['profile_pic_url'] = get_letter_profile_pic(username, db)
                    
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
            # If the username exists but doesn't have a profile picture, set one
            if not user.profile_pic_url:
                user.profile_pic_url = get_letter_profile_pic(user.username, db)
                
            # Update existing user
            for key, value in user_data.items():
                if value is not None and key not in ['badge_id', 'badge_image_url', 'profile_pic_url']:  # Don't overwrite existing badge and profile pic
                    setattr(user, key, value)
            
            # Store refresh token
            if tokens.refresh_token:
                user.refresh_token = tokens.refresh_token
            
            # Commit changes
            db.commit()
        
        # Get badge information if assigned
        badge_id = None
        badge_name = None
        badge_image_url = None
        
        if user.badge_id:
            badge = db.query(Badge).filter(Badge.id == user.badge_id).first()
            if badge:
                badge_id = badge.id
                badge_name = badge.name
                badge_image_url = badge.image_url
        
        # Get avatar and frame URLs if selected
        avatar_url = None
        frame_url = None
        
        if user.selected_avatar_id:
            avatar = db.query(Avatar).filter(Avatar.id == user.selected_avatar_id).first()
            if avatar:
                avatar_url = avatar.image_url
        
        if user.selected_frame_id:
            frame = db.query(Frame).filter(Frame.id == user.selected_frame_id).first()
            if frame:
                frame_url = frame.image_url
        
        # Return token information
        return LoginResponse(
            access_token=tokens.access_token,
            user_info=UserInfo(
                username=user.username or email.split('@')[0],
                account_id=user.account_id,
                badge_id=badge_id,
                badge_name=badge_name,
                badge_image_url=badge_image_url if badge_image_url else user.badge_image_url,
                is_existing_user=is_existing_user,
                avatar_url=avatar_url,
                frame_url=frame_url
            )
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token processing error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")