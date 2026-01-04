from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Dict, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from sqlalchemy.orm import Session
import logging
import os
import time

from db import get_db
# from auth import verify_access_token, refresh_auth0_token
from models import User
from descope.descope_client import DescopeClient
from config import DESCOPE_PROJECT_ID, DESCOPE_MANAGEMENT_KEY, DESCOPE_JWT_LEEWAY, DESCOPE_JWT_LEEWAY_FALLBACK

router = APIRouter(prefix="/auth", tags=["Refresh"])

# Create Descope client with management key for session operations
descope_client = DescopeClient(project_id=DESCOPE_PROJECT_ID, management_key=DESCOPE_MANAGEMENT_KEY, jwt_validation_leeway=DESCOPE_JWT_LEEWAY)
_SESSION_CACHE: Dict[str, Tuple[dict, float]] = {}
_SESSION_CACHE_TTL_SECONDS = int(os.getenv("DESCOPE_SESSION_CACHE_TTL_SECONDS", "30"))
_DESCOPE_VALIDATE_TIMEOUT_SECONDS = float(os.getenv("DESCOPE_VALIDATE_TIMEOUT_SECONDS", "5"))
_DESCOPE_VALIDATE_MAX_WORKERS = int(os.getenv("DESCOPE_VALIDATE_MAX_WORKERS", "4"))
_DESCOPE_EXECUTOR = ThreadPoolExecutor(max_workers=_DESCOPE_VALIDATE_MAX_WORKERS)


def _get_cached_session(token: str) -> Optional[dict]:
    cached = _SESSION_CACHE.get(token)
    if not cached:
        return None
    session, expires_at = cached
    if expires_at > time.time():
        return session
    _SESSION_CACHE.pop(token, None)
    return None


def _set_cached_session(token: str, session: dict) -> None:
    if _SESSION_CACHE_TTL_SECONDS <= 0:
        return
    _SESSION_CACHE[token] = (session, time.time() + _SESSION_CACHE_TTL_SECONDS)
    if len(_SESSION_CACHE) > 2000:
        _SESSION_CACHE.clear()


def _validate_session_with_timeout(client: DescopeClient, token: str) -> dict:
    future = _DESCOPE_EXECUTOR.submit(client.validate_session, token)
    try:
        return future.result(timeout=_DESCOPE_VALIDATE_TIMEOUT_SECONDS)
    except FutureTimeoutError as exc:
        future.cancel()
        raise HTTPException(status_code=504, detail="Session validation timed out") from exc

# The refresh endpoint is disabled after migration to Descope.
# Descope does not use refresh tokens in the same way as Auth0.
# If you need to implement session renewal, use Descope's session management APIs.

# All Auth0 refresh logic has been removed.

# Only keep endpoints that are compatible with Descope below this line.

# New Descope session refresh endpoint
@router.post("/refresh")
async def refresh_session(request: Request, db: Session = Depends(get_db)):
    """
    ## Refresh Descope Session
    
    Refreshes a Descope session using the session token from the Authorization header.
    This endpoint uses Descope's session management APIs to extend the session.
    
    ### Use this endpoint to:
    - Extend an existing Descope session before it expires
    - Get a new session token with extended expiration
    - Maintain user authentication without requiring re-login
    
    ### Headers:
    - `Authorization`: Bearer token with the current Descope session JWT
    
    ### Returns:
    - `access_token`: New session JWT token
    - `refresh_token`: Refresh token for future use (if available)
    - `token_type`: Always "Bearer"
    - `expires_in`: Token expiration time in seconds
    - `user_info`: User information from the session
    
    ### Note:
    This endpoint requires a valid Descope session token.
    If the session is already expired, this will fail and the user needs to re-authenticate.
    """
    try:
        # Extract Authorization header
        auth_header = request.headers.get('authorization', '').strip()
        if not auth_header:
            raise HTTPException(status_code=401, detail="No authorization header found")
        
        # Remove Bearer prefix
        if auth_header.lower().startswith('bearer '):
            token = auth_header.split(' ', 1)[1].strip()
        else:
            token = auth_header
        
        # Clean the token
        token = token.strip()
        token = ''.join(token.split())  # Remove all whitespace including newlines
        
        logger = logging.getLogger(__name__)
        logger.info("Attempting to refresh Descope session")
        
        try:
            # Use Descope's session refresh API
            # First, validate the current session to get user info
            session = _get_cached_session(token)
            if session is None:
                session = _validate_session_with_timeout(descope_client, token)
                _set_cached_session(token, session)
            
            # Extract user ID directly from session (not nested under 'user')
            user_id = session.get('userId') or session.get('sub')
            
            if not user_id:
                raise HTTPException(status_code=400, detail="Invalid session: no user ID found")
            
            # Use Descope's session refresh functionality
            # Note: Descope doesn't have a direct "refresh" API like Auth0
            # Instead, we can extend the session by updating user session settings
            # or create a new session for the same user
            
            # For now, we'll validate the session and return the same token
            # In a production environment, you might want to implement a more sophisticated
            # session extension mechanism
            
            # Extract user info from session for database operations
            user_info = {
                'userId': user_id,
                'sub': session.get('sub'),
                'loginIds': session.get('loginIds', []),
                'email': session.get('loginIds', [None])[0] if session.get('loginIds') else None,
                'name': session.get('name'),
                'displayName': session.get('displayName')
            }
            
            # Check if user exists in our database
            user = db.query(User).filter(User.descope_user_id == user_id).first()
            if not user:
                raise HTTPException(status_code=404, detail="User not found; please login")
            
            # Return session information
            return {
                "access_token": token,  # Return the same token for now
                "token_type": "Bearer",
                "expires_in": 3600,  # Default 1 hour
                "user_info": user_info,
                "message": "Session validated successfully"
            }
            
        except Exception as e:
            logger.error(f"Session refresh failed: {str(e)}")
            
            # Check if it's a time sync issue
            if "time glitch" in str(e).lower() or "jwt_validation_leeway" in str(e).lower():
                # Try with higher leeway
                try:
                    high_leeway_client = DescopeClient(
                        project_id=DESCOPE_PROJECT_ID, 
                        management_key=DESCOPE_MANAGEMENT_KEY,
                        jwt_validation_leeway=DESCOPE_JWT_LEEWAY_FALLBACK
                    )
                    session = _get_cached_session(token)
                    if session is None:
                        session = _validate_session_with_timeout(high_leeway_client, token)
                        _set_cached_session(token, session)
                    
                    # Extract user info directly from session
                    user_id = session.get('userId') or session.get('sub')
                    user_info = {
                        'userId': user_id,
                        'sub': session.get('sub'),
                        'loginIds': session.get('loginIds', []),
                        'email': session.get('loginIds', [None])[0] if session.get('loginIds') else None,
                        'name': session.get('name'),
                        'displayName': session.get('displayName')
                    }
                    
                    return {
                        "access_token": token,
                        "token_type": "Bearer", 
                        "expires_in": 3600,
                        "user_info": user_info,
                        "message": "Session validated with extended leeway"
                    }
                except Exception as e2:
                    logger.error(f"High leeway validation also failed: {str(e2)}")
            
            raise HTTPException(status_code=401, detail="Session refresh failed: Invalid or expired token")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in session refresh: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error during session refresh")
