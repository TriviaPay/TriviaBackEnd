from fastapi import APIRouter, Depends, HTTPException, status, Header, Request
from typing import Optional, List
from sqlalchemy.orm import Session
import logging
import json
from fastapi.openapi.models import Response
from fastapi import responses
import base64

from db import get_db
from auth import verify_access_token, refresh_auth0_token
from models import User
from routers.dependencies import get_current_user

router = APIRouter(prefix="/auth", tags=["Refresh"])

@router.post(
    "/refresh",
    summary="Refresh access token",
    description="""
    Exchanges your current access token for a new one using your refresh token.
    
    **Important Steps in Swagger UI**: 
    1. Click the green "Authorize" button at the top of this page
    2. In the authorization popup, enter your token in EXACTLY this format:
       ```
       Bearer eyJhbGciOiJSUzI1NiIs...rest_of_your_token
       ```
       - Must start with "Bearer " (including the space)
       - Paste your token immediately after "Bearer "
       - No extra spaces or line breaks
    3. Click "Authorize" in the popup
    4. Click "Close" in the popup
    5. Expand this /refresh endpoint
    6. Click "Try it out"
    7. Click "Execute"
    
    Common Issues:
    - Make sure you're using the global Authorize button at the top, not trying to add the token in the endpoint
    - Don't include quotes around the "Bearer your_token" text
    - Make sure there's exactly one space between "Bearer" and your token
    - Use your access token, not your refresh token
    - This endpoint will work with expired tokens, so it's okay if your token has expired
    
    The refresh token is stored securely in the database and will be used automatically.
    """,
    responses={
        200: {
            "description": "Successfully refreshed token",
            "content": {
                "application/json": {
                    "example": {
                        "access_token": "eyJhbGciOiJSUzI1...",
                        "token_type": "Bearer",
                        "expires_in": 86400
                    }
                }
            }
        },
        401: {
            "description": "Unauthorized - Invalid or missing token",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Authorization header is missing. Please click the 'Authorize' button at the top of the page and enter 'Bearer your_token_here'"
                    }
                }
            }
        },
        404: {
            "description": "User not found",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "User not found in database. Please ensure you're using the correct token."
                    }
                }
            }
        }
    }
)
def refresh_access_token(request: Request, db: Session = Depends(get_db)):
    """
    Client sends old or nearly-expired 'access_token' in Authorization header.
    We decode to find 'sub', fetch user's refresh_token, call Auth0 for a new access_token.
    Return the new access_token (and store new refresh_token if provided).
    """
    logger = logging.getLogger(__name__)
    
    try:
        # Extract Authorization header
        auth_header = request.headers.get('authorization', '').strip()
        if not auth_header:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authorization header missing. Please provide a Bearer token.",
                headers={"WWW-Authenticate": "Bearer"}
            )
        
        # Remove Bearer prefix and clean token
        if auth_header.lower().startswith('bearer '):
            token = auth_header.split(' ', 1)[1].strip()
        else:
            token = auth_header
            
        # Clean the token: remove any whitespace, newlines or extra characters
        token = token.strip()
        token = ''.join(token.split())  # Remove all whitespace including newlines
        
        # Additional debug logging
        logger.debug(f"Token parts analysis:")
        logger.debug(f"Token length: {len(token)}")
        logger.debug(f"Number of periods: {token.count('.')}")
        
        # Import libraries needed for decoding
        import base64
        import json
        
        # Make sure we only have a properly formatted JWT
        if token.count('.') != 2:
            # Try to recover the token if possible
            parts = token.split('.')
            logger.debug(f"Raw parts found: {len(parts)}, lengths: {[len(p) for p in parts]}")
            
            if len(parts) > 3:
                # Try to fix common issues where the token might have extra periods
                # but is still recoverable based on the parts lengths
                header_part = parts[0]
                payload_parts = []
                sig_parts = []
                
                # Typical header is fairly short
                # Typical payload is longer
                # Typical signature is fixed length and comes at the end
                
                # Simple heuristic: find the longest part for payload, last part for signature
                longest_part_index = max(range(1, len(parts)-1), key=lambda i: len(parts[i]))
                
                # Reconstruct a 3-part token
                reconstructed_token = f"{header_part}.{parts[longest_part_index]}.{parts[-1]}"
                logger.debug(f"Reconstructed token with 3 parts: {reconstructed_token[:20]}...")
                token = reconstructed_token
                parts = token.split('.')
                
                if len(parts) != 3:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail=f"Invalid token format: Unable to fix token structure. Expected 3 parts, got {len(parts)} originally",
                        headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""}
                    )
            else:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Invalid token format: expected 3 parts (header.payload.signature), got {len(parts)}",
                    headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""}
                )
        else:
            parts = token.split('.')
        
        # Proceed with decoded parts
        # Decode the payload (second part)
        payload_b64 = parts[1]
        # Handle padding
        payload_b64 += '=' * (4 - len(payload_b64) % 4) if len(payload_b64) % 4 != 0 else ''
        payload_json = base64.b64decode(payload_b64).decode('utf-8')
        payload = json.loads(payload_json)
        
        # Check for sub claim
        if 'sub' not in payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing 'sub' claim",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""}
            )
        
        # Get the user from the database
        user = db.query(User).filter(User.sub == payload['sub']).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User not found with sub: {payload['sub']}",
            )
        
        if not user.refresh_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No refresh token available for this user. Please log in again to obtain a new refresh token."
            )
        
        # Call Auth0 to refresh the token
        try:
            # Get new tokens from Auth0
            new_tokens = refresh_auth0_token(user.refresh_token)
            
            # Update user's refresh token if a new one was provided
            if new_tokens.get('refresh_token'):
                user.refresh_token = new_tokens['refresh_token']
                db.commit()
            
            return {
                "access_token": new_tokens['access_token'],
                "token_type": "Bearer",
                "expires_in": new_tokens.get('expires_in', 86400)
            }
        except Exception as e:
            logger.error(f"Token refresh error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Could not refresh token: {str(e)}",
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""}
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in refresh endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

# Add a new test endpoint:
@router.post("/test-token")
async def test_token(request: Request):
    """
    Test endpoint to debug token handling
    """
    try:
        # Extract Authorization header
        auth_header = request.headers.get('authorization', '').strip()
        if not auth_header:
            return {"error": "No authorization header found"}
        
        # Log full auth header for debugging
        logger = logging.getLogger(__name__)
        logger.debug(f"Full auth header received: {auth_header}")
        
        # Check for newlines or special characters
        has_newlines = '\n' in auth_header or '\r' in auth_header
        special_chars = [c for c in auth_header if not c.isalnum() and c not in ' .{}[]"\':-_+/=']
        
        # Remove Bearer prefix
        if auth_header.lower().startswith('bearer '):
            token = auth_header.split(' ', 1)[1].strip()
        else:
            token = auth_header
        
        # Basic token cleanup
        token = token.strip()
        token = ''.join(token.split())  # Remove all whitespace
        
        # Try to decode token directly without verification (just to see payload)
        import jwt
        import base64
        import json
        
        # Debug token format
        token_analysis = {
            "length": len(token),
            "period_count": token.count('.'),
            "has_newlines_in_original": has_newlines,
            "special_chars_in_original": special_chars,
            "token_snippet": token[:50] + "..." if len(token) > 50 else token
        }
        
        # Split the token
        parts = token.split('.')
        if len(parts) != 3:
            return {
                "error": f"Invalid token format: expected 3 parts, got {len(parts)}",
                "token_analysis": token_analysis,
                "parts_lengths": [len(p) for p in parts]
            }
        
        # Decode the payload (second part)
        payload_b64 = parts[1]
        # Handle padding
        payload_b64 += '=' * (4 - len(payload_b64) % 4) if len(payload_b64) % 4 != 0 else ''
        try:
            payload_json = base64.b64decode(payload_b64).decode('utf-8')
            payload = json.loads(payload_json)
        except Exception as e:
            return {"error": f"Failed to decode payload: {str(e)}", "token": token[:20] + "..."}
        
        # Get the header as well
        header_b64 = parts[0]
        header_b64 += '=' * (4 - len(header_b64) % 4) if len(header_b64) % 4 != 0 else ''
        try:
            header_json = base64.b64decode(header_b64).decode('utf-8')
            header = json.loads(header_json)
        except Exception as e:
            header = {"error": f"Failed to decode header: {str(e)}"}
        
        # Database check
        from db import get_db
        from models import User
        db = next(get_db())
        
        user = None
        if payload.get('sub'):
            user = db.query(User).filter(User.sub == payload['sub']).first()
            user_data = {
                "found": user is not None,
                "sub": user.sub if user else None,
                "has_refresh_token": bool(user.refresh_token) if user else False
            }
        else:
            user_data = {"error": "No sub claim in token"}
        
        # Return all information for debugging
        return {
            "token_info": {
                "header": header,
                "payload": payload
            },
            "user_data": user_data,
            "auth_header": auth_header[:20] + "..." if auth_header else None
        }
        
    except Exception as e:
        import traceback
        return {
            "error": f"Exception processing token: {str(e)}",
            "traceback": traceback.format_exc()
        }

# Add a new simplified refresh endpoint:
@router.post("/refresh-direct")
async def refresh_token_direct(request: Request):
    """
    Simplified refresh endpoint that directly extracts the token from the Authorization header
    and uses it to refresh the token
    """
    logger = logging.getLogger(__name__)
    
    try:
        # Extract Authorization header
        auth_header = request.headers.get('authorization', '').strip()
        if not auth_header:
            return {"error": "No authorization header found"}
        
        # Log full auth header for debugging
        logger.debug(f"Full auth header received in refresh-direct: {auth_header[:50]}...")
        
        # Remove Bearer prefix
        if auth_header.lower().startswith('bearer '):
            token = auth_header.split(' ', 1)[1].strip()
        else:
            token = auth_header
            
        # Clean the token: remove any whitespace, newlines or extra characters
        token = token.strip()
        token = ''.join(token.split())  # Remove all whitespace including newlines
        
        # Additional debug logging
        logger.debug(f"Token parts analysis:")
        logger.debug(f"Token length: {len(token)}")
        logger.debug(f"Number of periods: {token.count('.')}")
        
        # Import libraries needed for decoding
        import base64
        import json
        
        # Make sure we only have a properly formatted JWT
        if token.count('.') != 2:
            # Try to recover the token if possible
            parts = token.split('.')
            logger.debug(f"Raw parts found: {len(parts)}, lengths: {[len(p) for p in parts]}")
            
            if len(parts) > 3:
                # Try to fix common issues where the token might have extra periods
                header_part = parts[0]
                
                # Simple heuristic: find the longest part for payload, last part for signature
                longest_part_index = max(range(1, len(parts)-1), key=lambda i: len(parts[i]))
                
                # Reconstruct a 3-part token
                reconstructed_token = f"{header_part}.{parts[longest_part_index]}.{parts[-1]}"
                logger.debug(f"Reconstructed token with 3 parts: {reconstructed_token[:20]}...")
                token = reconstructed_token
                parts = token.split('.')
                
                if len(parts) != 3:
                    return {
                        "error": f"Invalid token format: Unable to fix token structure. Expected 3 parts, got {len(parts)} originally", 
                        "parts_count": len(parts)
                    }
            else:
                return {
                    "error": f"Invalid token format: expected 3 parts (header.payload.signature), got {len(parts)}",
                    "parts_count": len(parts),
                    "token_preview": token[:50] + "..." if len(token) > 50 else token
                }
        else:
            parts = token.split('.')
        
        # Proceed with decoded parts
        # Decode the payload (second part)
        payload_b64 = parts[1]
        # Handle padding
        payload_b64 += '=' * (4 - len(payload_b64) % 4) if len(payload_b64) % 4 != 0 else ''
        payload_json = base64.b64decode(payload_b64).decode('utf-8')
        payload = json.loads(payload_json)
        
        # Check for sub claim
        if 'sub' not in payload:
            return {"error": "Token payload doesn't contain 'sub' claim"}
        
        # Get the user from the database
        from db import get_db
        from models import User
        db = next(get_db())
        
        user = db.query(User).filter(User.sub == payload['sub']).first()
        if not user:
            return {"error": f"User not found with sub: {payload['sub']}"}
        
        if not user.refresh_token:
            return {"error": "No refresh token available for this user"}
        
        # Call Auth0 to refresh the token
        from auth import refresh_auth0_token
        
        try:
            # Get new tokens from Auth0
            new_tokens = refresh_auth0_token(user.refresh_token)
            
            # Update user's refresh token if a new one was provided
            if new_tokens.get('refresh_token'):
                user.refresh_token = new_tokens['refresh_token']
                db.commit()
            
            return {
                "access_token": new_tokens['access_token'],
                "token_type": "Bearer",
                "expires_in": new_tokens.get('expires_in', 86400)
            }
        except Exception as e:
            import traceback
            return {
                "error": f"Failed to refresh token: {str(e)}",
                "traceback": traceback.format_exc()
            }
            
    except Exception as e:
        import traceback
        return {
            "error": f"Exception processing token: {str(e)}",
            "traceback": traceback.format_exc()
        }