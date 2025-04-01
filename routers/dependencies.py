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

def get_current_user(request: Request):
    """
    Get the current authenticated user from the request
    
    Args:
        request (Request): The incoming HTTP request
    
    Returns:
        dict: User claims from the JWT token
    
    Raises:
        HTTPException: If authentication fails
    """
    # Log full request details for debugging
    logging.debug("=== Request Debug Information ===")
    logging.debug(f"Request Method: {request.method}")
    logging.debug(f"Request URL: {request.url}")
    
    # Log request headers
    headers = dict(request.headers)
    logging.debug("Request Headers:\n%s", json.dumps(headers, indent=2))
    
    # Extract Authorization header
    auth_header = request.headers.get('authorization', '').strip()
    logging.info(f"Found Authorization header using key: authorization")
    logging.info(f"Received Authorization header: {auth_header}")
    
    # Remove all 'Bearer ' prefixes (case-insensitive)
    while auth_header.lower().startswith('bearer '):
        auth_header = auth_header.split(' ', 1)[1].strip()
    
    # Clean up the token if it contains extra JSON-like content
    if '"refresh_token"' in auth_header:
        # Extract just the access token part
        auth_header = auth_header.split('"refresh_token"')[0].strip().rstrip(',').rstrip('"')
        logging.info(f"Cleaned token: {auth_header}")
    
    logging.info(f"Extracted token: {auth_header[:10]}... (truncated)")
    
    # Verify the token
    try:
        claims = verify_access_token(auth_header)
        
        # Log the claims for debugging
        logging.debug(f"Token claims: {json.dumps(claims, indent=2)}")
        
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
