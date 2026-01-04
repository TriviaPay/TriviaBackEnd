import logging
import json
from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from typing import Optional
from db import get_db
from auth import validate_descope_jwt
from models import User
import os

logger = logging.getLogger(__name__)

def get_current_user(request: Request, db=Depends(get_db)):
    """
    Extracts and validates Descope JWT from Authorization header. Returns user info dict.
    Users must be created through the /bind-password endpoint, not automatically here.
    """
    auth_header = request.headers.get('authorization') or request.headers.get('Authorization')
    if not auth_header or not auth_header.lower().startswith('bearer '):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization token missing.")
    token = auth_header.split(' ', 1)[1].strip()
    user_info = validate_descope_jwt(token)
    
    # Find user in DB by Descope user ID
    user = db.query(User).filter(User.descope_user_id == user_info['userId']).first()
    if not user:
        # Check if user exists by email (for users created before Descope integration)
        email = user_info['loginIds'][0]
        existing_user = db.query(User).filter(User.email == email).first()
        if existing_user:
            # Update existing user with Descope user ID but don't change username
            existing_user.descope_user_id = user_info['userId']
            db.commit()
            db.refresh(existing_user)
            user = existing_user
        else:
            # User doesn't exist - they need to complete profile binding
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, 
                detail="User profile not found. Please complete profile setup first."
            )
    return user

def validate_jwt_dependency(request: Request):
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    token = auth_header.split(" ", 1)[1].strip()
    return validate_descope_jwt(token)

def get_current_user_simple(claims: dict = Depends(validate_jwt_dependency)):
    return claims

def is_admin(user: User) -> bool:
    return bool(user.is_admin)

def verify_admin(user: User):
    if not is_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required for this endpoint"
        )

def get_admin_user(request: Request, db: Session = Depends(get_db)):
    """Verify user is admin using cosmetics.py logic"""
    user = get_current_user(request, db)
    verify_admin(user)
    return user
