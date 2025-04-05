import logging
import json
from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from typing import Optional
from db import get_db
from auth import verify_access_token
from models import User

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
    
    # Extract Authorization header
    auth_header = request.headers.get('authorization', '').strip()
    if not auth_header:
        logging.error("No Authorization header found in request")
        auth_header = request.headers.get('Authorization', '').strip()
        if auth_header:
            logging.info("Found Authorization header with uppercase key")
        else:
            logging.error("No Authorization header found in request (case insensitive)")
            # Log all headers for debugging
            logging.error(f"All headers: {json.dumps(dict(request.headers), indent=2)}")
    
    logging.info(f"Found Authorization header: {auth_header[:20]}..." if auth_header else "No Authorization header")
    
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing. Please provide a Bearer token.",
            headers={"WWW-Authenticate": "Bearer"}
        )
        
    # Remove all 'Bearer ' prefixes (case-insensitive)
    orig_auth_header = auth_header
    while auth_header.lower().startswith('bearer '):
        auth_header = auth_header.split(' ', 1)[1].strip()
    
    if orig_auth_header != auth_header:
        logging.info(f"Stripped Bearer prefix: {auth_header[:20]}...")
    
    # Clean up the token if it contains extra JSON-like content
    if '"refresh_token"' in auth_header:
        # Extract just the access token part
        auth_header = auth_header.split('"refresh_token"')[0].strip().rstrip(',').rstrip('"')
        logging.info(f"Cleaned token: {auth_header[:20]}...")
    
    logging.info(f"Final token to verify: {auth_header[:20]}... with check_expiration={check_expiration}, require_email={require_email}")
    
    # Verify the token
    try:
        logging.info(f"Calling verify_access_token with check_expiration={check_expiration}, require_email={require_email}")
        claims = verify_access_token(auth_header, check_expiration=check_expiration, require_email=require_email)
        
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
