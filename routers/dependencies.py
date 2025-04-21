import logging
import json
from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from typing import Optional
from db import get_db
from auth import verify_access_token
from models import User
import os

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def get_current_user(request: Request, check_expiration: bool = True, require_email: bool = True):
    """
    Get the current authenticated user from the request
    
    Args:
        request (Request): The incoming HTTP request
        check_expiration (bool): Whether to check token expiration. Defaults to True.
                                Set to False for endpoints like refresh that need to work with expired tokens.
        require_email (bool): Whether to require email verification. Defaults to True.
    
    Returns:
        dict: User claims from the JWT token
    
    Raises:
        HTTPException: If authentication fails
    """
    # Special handling for profile endpoints - allow expired tokens and skip email requirement
    if "/profile/" in str(request.url):
        check_expiration = False
        require_email = False
        logging.info(f"Profile endpoint detected: {request.url} - Disabling token expiration check and email requirement")
    else:
        logging.info(f"Regular endpoint (non-profile): {request.url} - Using check_expiration={check_expiration}, require_email={require_email}")
        
    # Log full request details for debugging
    logging.debug("=== Request Debug Information ===")
    logging.debug(f"Request Method: {request.method}")
    logging.debug(f"Request URL: {request.url}")
    
    # Log request headers
    headers = dict(request.headers)
    logging.debug("Request Headers:\n%s", json.dumps(headers, indent=2))
    
    # Try to get token from different sources
    token = None
    
    # First, check Authorization header (most common)
    auth_header = request.headers.get('authorization', '').strip()
    if not auth_header:
        auth_header = request.headers.get('Authorization', '').strip()
    
    if auth_header:
        logging.info(f"Found Authorization header: {auth_header[:20]}..." if len(auth_header) > 20 else auth_header)
        # Remove all 'Bearer ' prefixes (case-insensitive)
        orig_auth_header = auth_header
        while auth_header.lower().startswith('bearer '):
            auth_header = auth_header.split(' ', 1)[1].strip()
        
        if orig_auth_header != auth_header:
            logging.info(f"Stripped Bearer prefix: {auth_header[:20]}..." if len(auth_header) > 20 else auth_header)
        
        token = auth_header
    
    # If no authorization header, try from query params
    if not token:
        token_from_query = request.query_params.get('access_token')
        if token_from_query:
            logging.info(f"Found token from query parameter: {token_from_query[:20]}..." if len(token_from_query) > 20 else token_from_query)
            token = token_from_query
    
    # If still no token, try from form data - synchronous approach
    if not token and request.method in ["POST", "PUT", "PATCH"]:
        try:
            # Check if content type is form
            if "application/x-www-form-urlencoded" in request.headers.get("content-type", ""):
                # Since we can't use await here, we'll just check if the request has this attribute
                # FastAPI might have already parsed the form data
                if hasattr(request, "form") and request.form and "access_token" in request.form:
                    token_from_form = request.form.get("access_token")
                    logging.info(f"Found token from form data: {token_from_form[:20]}...")
                    token = token_from_form
        except Exception as e:
            logging.error(f"Error accessing form data: {e}")
    
    # If no token found, raise exception
    if not token:
        logging.error("No Authorization token found in request (checked headers, query params, and form data)")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization token missing. Please provide a Bearer token in the header, query parameter, or form data.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    # Clean up the token if it contains extra JSON-like content
    if '"refresh_token"' in token:
        # Extract just the access token part
        token = token.split('"refresh_token"')[0].strip().rstrip(',').rstrip('"')
        logging.info(f"Cleaned token: {token[:20]}...")
    
    logging.info(f"Final token to verify: {token[:20]}... with check_expiration={check_expiration}, require_email={require_email}")
    
    # Verify the token
    try:
        logging.info(f"Calling verify_access_token with check_expiration={check_expiration}, require_email={require_email}")
        claims = verify_access_token(token, check_expiration=check_expiration, require_email=require_email)
        
        # Log the claims for debugging
        logging.info(f"Successfully verified token, got claims for sub: {claims.get('sub', 'NO SUB FOUND')}")
        
        # Ensure sub claim is present
        if not claims.get('sub'):
            logging.error("No sub claim found in token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, 
                detail="Missing user identifier",
                headers={"WWW-Authenticate": "Bearer"}
            )
        
        return claims
    except Exception as e:
        logging.error(f"Authentication failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"}
        )

def get_current_user_simple(claims: dict = Depends(verify_access_token)):
    """Get current user from verified token"""
    return claims

def is_admin(current_user: dict, db: Session) -> bool:
    """
    Check if the current user is an admin based on their email matching ADMIN_EMAIL in env
    or their sub claim matching an authorized admin
    
    Args:
        current_user (dict): The current user's JWT claims
        db (Session): Database session
        
    Returns:
        bool: Whether the user is an admin
    """
    # Get admin email from environment or use default
    admin_email = os.getenv("ADMIN_EMAIL", "triviapay3@gmail.com")
    
    # Log detailed info about admin checks
    logger.info(f"Checking admin status for user with sub: {current_user.get('sub')}, email: {current_user.get('email')}")
    
    # Get list of authorized admin subs from env or use default
    admin_subs_str = os.getenv("ADMIN_SUBS", "email|67c00b4245db3e9383e93bf")
    admin_subs = [sub.strip() for sub in admin_subs_str.split(',')]
    
    # Check if sub is in the list of admin subs
    sub = current_user.get('sub')
    if sub and sub in admin_subs:
        logger.info(f"User authorized as admin based on sub: {sub}")
        return True
    
    # Admin check is based on email
    email = current_user.get('email')
    if email and email.lower() == admin_email.lower():
        logger.info(f"User authorized as admin based on email: {email}")
        return True
        
    # Check in database
    if email:
        user = db.query(User).filter(User.email == email).first()
        if user and user.email.lower() == admin_email.lower():
            logger.info(f"User authorized as admin based on database email: {user.email}")
            return True
    
    # If sub looks like an email reference, try to extract email part
    if sub and '|' in sub and sub.split('|')[0] == 'email':
        # Get the email identifier part and check against admin list
        email_id = sub.split('|')[1]
        for admin_sub in admin_subs:
            if admin_sub.endswith(email_id):
                logger.info(f"User authorized as admin based on email ID in sub: {email_id}")
                return True
    
    logger.info(f"User NOT authorized as admin")
    return False


def verify_admin(current_user: dict, db: Session) -> None:
    """
    Verify the user is an admin or raise an HTTP exception
    
    Args:
        current_user (dict): The current user's JWT claims
        db (Session): Database session
        
    Raises:
        HTTPException: If the user is not an admin
    """
    if not is_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required for this endpoint"
        )


def get_admin_user(
    request: Request,
    db: Session = Depends(get_db),
    claims: dict = Depends(get_current_user)  # Ensure this is a valid dependency
):
    logger = logging.getLogger(__name__)
    logger.info("Processing admin authentication")
    
    # Now verify admin status
    try:
        # Ensure email is present for admin check
        if not claims.get('email'):
            admin_email = os.getenv("ADMIN_EMAIL", "triviapay3@gmail.com")
            logger.info(f"Email missing from claims for admin check, admin_email={admin_email}")
            
            # Check if sub claim looks like an email
            sub = claims.get('sub', '')
            if sub and '|' in sub and sub.split('|')[0] == 'email':
                # Try looking up user in database by sub
                user = db.query(User).filter(User.sub == sub).first()
                if user and user.email:
                    claims['email'] = user.email
                    logger.info(f"Added email {user.email} from database lookup")
                else:
                    # If we can't find the user in the database, check if the sub is in admin_subs
                    admin_subs_str = os.getenv("ADMIN_SUBS", "email|67c00b4245db3e9383e93bf")
                    admin_subs = [sub.strip() for sub in admin_subs_str.split(',')]
                    if sub in admin_subs:
                        claims['email'] = admin_email
                        logger.info(f"Added admin email {admin_email} based on sub claim")
        
        # Log the claims before admin check
        logger.info(f"Checking admin status with claims: {claims}")
        
        # Verify admin status
        if not is_admin(claims, db):
            logger.error("Admin access denied: User is not an admin")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required for this endpoint"
            )
        
        logger.info(f"Admin access granted for user: {claims.get('email', 'unknown')}")
        return claims
    except HTTPException as e:
        logger.error(f"Admin access denied: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"Error checking admin status: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access check failed: " + str(e)
        )
