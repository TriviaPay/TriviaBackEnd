from fastapi import APIRouter, Depends, HTTPException, status, Header, Request
from typing import Optional, List
from sqlalchemy.orm import Session
import logging
import json

from db import get_db
from auth import verify_access_token, refresh_auth0_token
from models import User

router = APIRouter(prefix="/auth", tags=["Refresh"])

@router.post("/refresh")
def refresh_access_token(
    request: Request,
    authorization: Optional[str] = Header(None, alias="authorization"),
    db: Session = Depends(get_db)
):
    """
    Client sends old or nearly-expired 'access_token' in Authorization header.
    We decode to find 'sub', fetch user's refresh_token, call Auth0 for a new access_token.
    Return the new access_token (and store new refresh_token if provided).
    """
    # Add detailed logging for debugging
    logger = logging.getLogger(__name__)
    
    # Log ALL headers for comprehensive debugging
    logger.error("ALL HEADERS RECEIVED:")
    headers_dict = dict(request.headers)
    logger.error(json.dumps(headers_dict, indent=2))
    
    # Try multiple variations of the authorization header
    possible_headers = [
        authorization,  # Default header
        request.headers.get('authorization'),  # Lowercase from request headers
        request.headers.get('Authorization'),  # Uppercase from request headers
    ]
    
    # Find the first non-None authorization header
    authorization = next((h for h in possible_headers if h), None)
    
    # Log specific authorization header
    logger.error(f"Resolved Authorization header: {authorization}")
    
    # If no authorization header, log additional context
    if not authorization:
        logger.error("No Authorization header received")
        logger.error("Request headers may be missing or not properly set")
        
        # Log all headers for debugging
        logger.error("Full headers dictionary:")
        logger.error(json.dumps(headers_dict, indent=2))
        
        raise HTTPException(
            status_code=401, 
            detail="No Authorization header. To fix this:\n1. Click the 'Authorize' button at the top of the page\n2. Enter: 'Bearer YOUR_REFRESH_TOKEN'\n3. Click 'Authorize' and then 'Close'\n4. Try the request again"
        )

    # Validate authorization header format
    try:
        scheme, _, token = authorization.partition(" ")
        
        # Log parsing details
        logger.error(f"Parsed scheme: {scheme}")
        logger.error(f"Parsed token length: {len(token) if token else 'N/A'}")
        
        if not scheme or not token:
            logger.error(f"Invalid authorization header format. Full header: {authorization}")
            raise HTTPException(
                status_code=401, 
                detail="Invalid Authorization header format. Use 'Bearer <token>'"
            )

        if scheme.lower() != "bearer":
            logger.error(f"Invalid authorization scheme: {scheme}")
            raise HTTPException(
                status_code=401, 
                detail=f"Invalid auth scheme. Expected 'Bearer', got '{scheme}'"
            )

        # Attempt to decode the old token
        claims = verify_access_token(token, check_expiration=False)
        sub = claims.get("sub")
        if not sub:
            logger.error("Token has no 'sub' claim")
            raise HTTPException(status_code=401, detail="Token has no 'sub' claim")

        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            logger.error(f"No user found with sub: {sub}")
            raise HTTPException(status_code=401, detail="User not found in DB")

        if not user.refresh_token:
            logger.error("No refresh token stored for user")
            raise HTTPException(
                status_code=400,
                detail="No refresh token stored. Re-login with Auth0?"
            )

        # Refresh tokens
        new_tokens = refresh_auth0_token(user.refresh_token)
        new_access_token = new_tokens.get("access_token")
        new_refresh_token = new_tokens.get("refresh_token")

        if not new_access_token:
            logger.error("Could not obtain new access token")
            raise HTTPException(status_code=401, detail="Refresh token invalid/expired")

        # Update DB if Auth0 returns a new refresh_token
        if new_refresh_token and new_refresh_token != user.refresh_token:
            user.refresh_token = new_refresh_token
            db.commit()

        # Return full token details
        return {
            "access_token": new_access_token,
            "refresh_token": new_refresh_token or user.refresh_token,
            "token_type": new_tokens.get("token_type", "Bearer"),
            "expires_in": new_tokens.get("expires_in")
        }
    
    except Exception as e:
        logger.error(f"Unexpected error in refresh_access_token: {str(e)}")
        raise
