from fastapi import HTTPException
from descope.descope_client import DescopeClient
from config import DESCOPE_PROJECT_ID, DESCOPE_JWT_LEEWAY, DESCOPE_JWT_LEEWAY_FALLBACK, DESCOPE_MANAGEMENT_KEY
import logging
import json
import base64
import os

# Initialize Descope client with configurable leeway for time sync issues
client = DescopeClient(project_id=DESCOPE_PROJECT_ID, jwt_validation_leeway=DESCOPE_JWT_LEEWAY)
logging.info(f"Descope client initialized with JWT leeway: {DESCOPE_JWT_LEEWAY}s")

def decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload without verification for debugging purposes."""
    try:
        # Split the token and decode the payload (second part)
        parts = token.split('.')
        if len(parts) != 3:
            return {}
        
        # Add padding if needed
        payload = parts[1]
        padding = len(payload) % 4
        if padding:
            payload += '=' * (4 - padding)
        
        # Decode base64
        decoded_bytes = base64.urlsafe_b64decode(payload)
        return json.loads(decoded_bytes.decode('utf-8'))
    except Exception as e:
        logging.debug(f"Failed to decode JWT payload: {e}")
        return {}

def validate_descope_jwt(token: str) -> dict:
    """
    Validate Descope session JWT and return user info.
    In case of time skew issues, retry with a higher leeway.
    
    Args:
        token (str): Descope session JWT token
    
    Returns:
        dict: User information from the validated session
    
    Raises:
        HTTPException: If token validation fails or user info is missing
    """
    # Debug: decode token payload for inspection
    jwt_payload = decode_jwt_payload(token)
    logging.debug(f"JWT payload (decoded): {json.dumps(jwt_payload, indent=2, default=str)}")
    
    try:
        session = client.validate_session(token)
        
        # Debug logging to inspect session structure
        logging.debug(f"Descope session validation successful. Session keys: {list(session.keys()) if isinstance(session, dict) else 'Not a dict'}")
        logging.debug(f"Session payload: {json.dumps(session, indent=2, default=str) if isinstance(session, dict) else str(session)}")
        
        if not isinstance(session, dict):
            logging.error("Descope session validation failed: session is not a dictionary")
            raise HTTPException(status_code=401, detail="Invalid session format")
        
        # Extract user info - Descope returns user info directly in session, not nested under 'user'
        user_info = {
            'userId': session.get('userId') or session.get('sub'),
            'sub': session.get('sub'),
            'loginIds': [],  # Will be populated below
            'email': None,  # Will be populated below
            'name': None,
            'displayName': None
        }
        
        # Try to get email from various sources
        email = None
        if 'loginIds' in session and isinstance(session['loginIds'], list) and len(session['loginIds']) > 0:
            email = session['loginIds'][0]
            user_info['loginIds'] = session['loginIds']
        elif 'email' in session:
            email = session['email']
            user_info['loginIds'] = [email]
        
        # If we still don't have email, try to get user details from Descope management API
        if not email and user_info['userId'] and DESCOPE_MANAGEMENT_KEY:
            try:
                mgmt_client = DescopeClient(project_id=DESCOPE_PROJECT_ID, management_key=DESCOPE_MANAGEMENT_KEY, jwt_validation_leeway=DESCOPE_JWT_LEEWAY)
                # Use the correct method name - it should be 'load' not 'get_by_user_id'
                user_details = mgmt_client.mgmt.user.load(user_info['userId'])
                if user_details and isinstance(user_details, dict):
                    # Handle different response structures
                    user_data = user_details.get('user', user_details)
                    if 'loginIds' in user_data and isinstance(user_data['loginIds'], list) and len(user_data['loginIds']) > 0:
                        email = user_data['loginIds'][0]
                        user_info['loginIds'] = user_data['loginIds']
                    elif 'email' in user_data:
                        email = user_data['email']
                        user_info['loginIds'] = [email]
                    user_info['name'] = user_data.get('name')
                    user_info['displayName'] = user_data.get('displayName')
                    logging.debug(f"Retrieved user details from management API: {json.dumps(user_data, indent=2, default=str)}")
            except Exception as e:
                logging.warning(f"Could not fetch user details from management API: {e}")
        
        # If we still don't have email, create a placeholder based on userId
        if not email:
            email = f"user_{user_info['userId']}@descope.local"
            user_info['loginIds'] = [email]
            logging.warning(f"No email found for user {user_info['userId']}, using placeholder: {email}")
        
        user_info['email'] = email
        
        # Validate that we have the minimum required info
        if not user_info['userId']:
            logging.error("Descope JWT validation failed: missing userId in session")
            raise HTTPException(status_code=401, detail="Invalid token: missing user ID")
            
        logging.debug(f"Final user_info: {json.dumps(user_info, indent=2, default=str)}")
        return user_info
        
    except HTTPException:
        # Re-raise HTTPExceptions as-is
        raise
    except Exception as e:
        logging.error(f"Descope JWT validation failed: {e}")
        
        # Retry with higher leeway if configured
        try:
            logging.info(f"Retrying JWT validation with fallback leeway: {DESCOPE_JWT_LEEWAY_FALLBACK}s")
            high_leeway_client = DescopeClient(
                project_id=DESCOPE_PROJECT_ID,
                jwt_validation_leeway=DESCOPE_JWT_LEEWAY_FALLBACK
            )
            session = high_leeway_client.validate_session(token)
            
            # Debug logging for fallback attempt
            logging.debug(f"High leeway validation successful. Session keys: {list(session.keys()) if isinstance(session, dict) else 'Not a dict'}")
            
            if not isinstance(session, dict):
                logging.error("High leeway validation failed: session is not a dictionary")
                raise HTTPException(status_code=401, detail="Invalid session format")
            
            # Same user info extraction logic for fallback
            user_info = {
                'userId': session.get('userId') or session.get('sub'),
                'sub': session.get('sub'),
                'loginIds': session.get('loginIds', []),
                'email': session.get('email'),
                'name': session.get('name'),
                'displayName': session.get('displayName')
            }
            
            # Create placeholder email if missing
            if not user_info['email'] and user_info['userId']:
                user_info['email'] = f"user_{user_info['userId']}@descope.local"
                user_info['loginIds'] = [user_info['email']]
            
            if not user_info['userId']:
                logging.error("High leeway validation failed: missing userId in session")
                raise HTTPException(status_code=401, detail="Invalid token: missing user ID")
                
            return user_info
        except HTTPException:
            raise
        except Exception as e2:
            logging.error(f"High leeway validation also failed: {e2}")
            raise HTTPException(status_code=401, detail="Invalid or expired token") 