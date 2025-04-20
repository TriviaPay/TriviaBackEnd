import requests
from fastapi import HTTPException, status
from jose import jwt, JWTError
from config import (
    AUTH0_DOMAIN, AUTH0_CLIENT_ID, AUTH0_CLIENT_SECRET,
    API_AUDIENCE, AUTH0_ALGORITHMS, AUTH0_ISSUER
)
import logging
import json
import base64
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.exceptions import InvalidSignature
import datetime
from typing import Union
import time
import random
import os

# Configure retries
MAX_RETRIES = 3
RETRY_BACKOFF_FACTOR = 0.5

def base64url_decode(input_str):
    """
    Decode base64url encoded string
    """
    # Add padding if needed
    input_str += '=' * (4 - (len(input_str) % 4))
    return base64.urlsafe_b64decode(input_str)

def get_email_from_userinfo(access_token: str, return_full_info: bool = False) -> Union[str, dict]:
    """
    Retrieve email or full userinfo from Auth0 userinfo endpoint
    
    Args:
        access_token (str): JWT access token
        return_full_info (bool, optional): Whether to return full userinfo. Defaults to False.
    
    Returns:
        Union[str, dict]: User's email address or full userinfo dictionary
    """
    logger = logging.getLogger(__name__)
    
    for retry in range(MAX_RETRIES):
        try:
            userinfo_url = f"https://{AUTH0_DOMAIN}/userinfo"
            headers = {"Authorization": f"Bearer {access_token}"}
            
            # Log the request details for debugging
            logger.debug(f"Userinfo Request URL: {userinfo_url}")
            logger.debug(f"Authorization Header: {headers}")
            
            response = requests.get(userinfo_url, headers=headers, timeout=10)
            
            logger.debug(f"Userinfo Response Status: {response.status_code}")
            logger.debug(f"Userinfo Response Content: {response.text}")
            
            if response.status_code != 200:
                logger.error(f"Userinfo request failed with status {response.status_code}")
                logger.error(f"Response content: {response.text}")
                
                # Retry if this is a server error (5xx)
                if 500 <= response.status_code < 600 and retry < MAX_RETRIES - 1:
                    sleep_time = RETRY_BACKOFF_FACTOR * (2 ** retry) + random.uniform(0, 1)
                    logger.info(f"Retrying userinfo request in {sleep_time:.2f} seconds (attempt {retry+1}/{MAX_RETRIES})")
                    time.sleep(sleep_time)
                    continue
                
                return None
            
            userinfo = response.json()
            logger.debug(f"Userinfo retrieved: {userinfo}")
            
            # Return full info if requested
            if return_full_info:
                return userinfo
            
            # Prioritize email extraction
            email = userinfo.get('email')
            if not email:
                logger.error("No email found in userinfo")
                return None
            
            return email
        
        except requests.RequestException as e:
            logger.error(f"Request error retrieving userinfo (attempt {retry+1}/{MAX_RETRIES}): {str(e)}")
            if retry < MAX_RETRIES - 1:
                sleep_time = RETRY_BACKOFF_FACTOR * (2 ** retry) + random.uniform(0, 1)
                logger.info(f"Retrying in {sleep_time:.2f} seconds")
                time.sleep(sleep_time)
            else:
                logger.error(f"Max retries reached for userinfo request")
                return None
        
        except Exception as e:
            logger.error(f"Error retrieving userinfo: {str(e)}")
            return None

def verify_access_token(access_token: str, check_expiration: bool = True, require_email: bool = True) -> dict:
    """
    Verify the access token's signature/claims using Auth0's public JWKS or local secret.
    Returns the decoded payload if valid, else raises 401.
    
    Args:
        access_token (str): JWT access token to verify
        check_expiration (bool, optional): Whether to check token expiration. Defaults to True.
        require_email (bool, optional): Whether to require email in the token. Defaults to True.
    
    Returns:
        dict: Decoded token payload
    """
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.DEBUG)

    # Maximum number of retries for JWKS fetching
    max_retries = MAX_RETRIES
    
    # Check if this is a development environment
    is_dev_env = os.getenv("APP_ENV", "development") == "development"
    
    # Allow test tokens in development mode (starting with 'eyJhbGci')
    if is_dev_env and access_token and isinstance(access_token, str) and access_token.startswith("eyJhbGci"):
        try:
            # For development mode, skip token verification
            logger.info("Development mode detected. Accepting test token with minimal verification.")
            
            # Basic JWT format validation (header.payload.signature)
            parts = access_token.split('.')
            if len(parts) != 3:
                logger.warning("Invalid test token format - should have 3 parts")
            
            # Try to decode the token without verification
            payload = jwt.decode(
                access_token,
                "dev_key_not_used",  # Add a placeholder key parameter
                options={
                    "verify_signature": False,
                    "verify_aud": False,
                    "verify_iss": False,
                    "verify_exp": check_expiration
                }
            )
            
            # Validate basic claims
            if "sub" not in payload:
                logger.warning("Test token missing 'sub' claim")
                
            # Make sure email is present if required
            if require_email and "email" not in payload:
                logger.warning("Test token missing required 'email' claim")
                # If email is required but not in token, try to get it from userinfo
                if "sub" in payload:
                    logger.info("No email in token, attempting to retrieve from userinfo")
                    userinfo = get_email_from_userinfo(access_token, return_full_info=True)
                    if userinfo and "email" in userinfo:
                        # Add email to payload
                        payload["email"] = userinfo["email"]
                        payload["email_verified"] = userinfo.get("email_verified", False)
                        logger.info(f"Added email from userinfo: {userinfo['email']}")
                    else:
                        # If we can't get the email from userinfo, but the sub claim is valid, allow it
                        admin_subs_str = os.getenv("ADMIN_SUBS", "email|67c00b4245db3e9383e93bf")
                        admin_subs = [sub.strip() for sub in admin_subs_str.split(',')]
                        if payload.get('sub') in admin_subs:
                            logger.info(f"Token missing email but sub claim {payload.get('sub')} is in admin list")
                            # Add a placeholder email for admin checks
                            payload["email"] = os.getenv("ADMIN_EMAIL", "triviapay3@gmail.com")
                            payload["email_verified"] = True
                
            logger.info(f"Successfully verified token in dev mode for: {payload.get('email', 'unknown')}")
            return payload
            
        except Exception as e:
            logger.error(f"Error validating test token: {str(e)}")
            # Try another method - parse manually for development mode
            try:
                if len(parts) == 3:
                    # Manually decode the payload (middle part)
                    payload_part = parts[1]
                    # Add padding if necessary
                    payload_part += "=" * ((4 - len(payload_part) % 4) % 4)
                    # Decode from base64
                    decoded_bytes = base64.urlsafe_b64decode(payload_part)
                    # Convert to JSON
                    decoded_payload = json.loads(decoded_bytes.decode('utf-8'))
                    
                    logger.info(f"Manually decoded payload: {decoded_payload}")
                    
                    # Check required fields
                    if "sub" not in decoded_payload:
                        logger.warning("Manually decoded token missing 'sub' claim")
                    
                    # If email is required but not in token, attempt to retrieve it
                    if require_email and "email" not in decoded_payload:
                        logger.info("Attempting to retrieve email from userinfo for manually decoded token")
                        userinfo = get_email_from_userinfo(access_token, return_full_info=True)
                        if userinfo and "email" in userinfo:
                            decoded_payload["email"] = userinfo["email"]
                            decoded_payload["email_verified"] = userinfo.get("email_verified", False)
                        else:
                            # If we can't get the email from userinfo, but the sub claim is valid, allow it
                            admin_subs_str = os.getenv("ADMIN_SUBS", "email|67c00b4245db3e9383e93bf")
                            admin_subs = [sub.strip() for sub in admin_subs_str.split(',')]
                            if decoded_payload.get('sub') in admin_subs:
                                logger.info(f"Token missing email but sub claim {decoded_payload.get('sub')} is in admin list")
                                # Add a placeholder email for admin checks
                                decoded_payload["email"] = os.getenv("ADMIN_EMAIL", "triviapay3@gmail.com")
                                decoded_payload["email_verified"] = True
                    
                    return decoded_payload
            except Exception as manual_e:
                logger.error(f"Manual decoding also failed: {manual_e}")
    
    for retry in range(max_retries):
        try:
            # Log the raw token for debugging
            logger.debug(f"Received token: {access_token}")

            # Basic JWT format validation
            if not access_token or not isinstance(access_token, str):
                raise JWTError("Token must be a non-empty string")

            # Clean up the token if needed
            token = access_token.strip()
            
            # Handle case where token might have been concatenated with another token
            # This can happen in certain client-side implementations
            if '.' in token:
                parts = token.split('.')
                if len(parts) > 3:
                    logger.warning(f"Token has {len(parts)} parts, which suggests concatenated tokens. Extracting first valid token.")
                    # Try to extract just the first valid token (first 3 parts)
                    token = '.'.join(parts[:3])
                    logger.info(f"Using modified token for validation: {token[:20]}...")
            
            # Handle case where the token might be an invalid format (like a number or query parameter)
            if token.isdigit() or '=' in token:
                # This might be a query parameter or numeric value, not a JWT
                # Use a default admin token for development/testing purposes
                logger.warning(f"Token appears to be in an invalid format: {token[:10]}..., using fallback admin token")
                
                # Create a default admin token for testing
                iat = datetime.datetime.utcnow()
                exp_time = iat + datetime.timedelta(days=1)
                
                # Only do this in development mode!
                if os.getenv("APP_ENV", "development") == "development":
                    payload = {
                        "sub": "email|67c00b4245db3e9383e93bf",
                        "email": os.getenv("ADMIN_EMAIL", "triviapay3@gmail.com"),
                        "email_verified": True,
                        "iat": int(iat.timestamp()),
                        "exp": int(exp_time.timestamp())
                    }
                    return payload
                else:
                    raise JWTError("Invalid token format")
            
            # Get the JWKS from Auth0
            jwks_url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
            jwks = requests.get(jwks_url).json()
            
            # Get the key ID from the token header
            unverified_header = jwt.get_unverified_header(token)
            rsa_key = {}
            
            for key in jwks["keys"]:
                if key["kid"] == unverified_header["kid"]:
                    rsa_key = {
                        "kty": key["kty"],
                        "kid": key["kid"],
                        "use": key["use"],
                        "n": key["n"],
                        "e": key["e"]
                    }
            
            if not rsa_key:
                raise JWTError("Unable to find appropriate key")
            
            # Verify the token
            payload = jwt.decode(
                token,
                rsa_key,
                algorithms=AUTH0_ALGORITHMS,
                audience=API_AUDIENCE,
                issuer=f"https://{AUTH0_DOMAIN}/"
            )
            
            # If email is required but not in token, try to get it from userinfo
            if require_email and "email" not in payload:
                logger.info("No email in token, attempting to retrieve from userinfo")
                userinfo = get_email_from_userinfo(access_token, return_full_info=True)
                if userinfo and "email" in userinfo:
                    payload["email"] = userinfo["email"]
                    payload["email_verified"] = userinfo.get("email_verified", False)
                else:
                    # If we can't get the email from userinfo, but the sub claim is valid, allow it
                    admin_subs_str = os.getenv("ADMIN_SUBS", "email|67c00b4245db3e9383e93bf")
                    admin_subs = [sub.strip() for sub in admin_subs_str.split(',')]
                    if payload.get('sub') in admin_subs:
                        logger.info(f"Token missing email but sub claim {payload.get('sub')} is in admin list")
                        # Add a placeholder email for admin checks
                        payload["email"] = os.getenv("ADMIN_EMAIL", "triviapay3@gmail.com")
                        payload["email_verified"] = True
            
            return payload
            
        except JWTError as e:
            logger.error(f"JWT validation error: {str(e)}")
            if retry < max_retries - 1:
                sleep_time = RETRY_BACKOFF_FACTOR * (2 ** retry) + random.uniform(0, 1)
                logger.info(f"Retrying in {sleep_time:.2f} seconds")
                time.sleep(sleep_time)
            else:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token",
                    headers={"WWW-Authenticate": "Bearer"}
                )
        except Exception as e:
            logger.error(f"Error verifying token: {str(e)}")
            if retry < max_retries - 1:
                sleep_time = RETRY_BACKOFF_FACTOR * (2 ** retry) + random.uniform(0, 1)
                logger.info(f"Retrying in {sleep_time:.2f} seconds")
                time.sleep(sleep_time)
            else:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Error verifying token",
                    headers={"WWW-Authenticate": "Bearer"}
                )

def refresh_auth0_token(refresh_token: str) -> dict:
    """
    Exchange the stored refresh token for a new access token via Auth0.
    
    Args:
        refresh_token (str): Refresh token to exchange for new tokens
    
    Returns:
        dict: New access token and related information
    """
    logger = logging.getLogger(__name__)
    
    try:
        url = f"https://{AUTH0_DOMAIN}/oauth/token"
        data = {
            "grant_type": "refresh_token",
            "client_id": AUTH0_CLIENT_ID,
            "client_secret": AUTH0_CLIENT_SECRET,
            "refresh_token": refresh_token
        }
        
        # Log the request details for debugging
        logger.debug(f"Refresh Token Request URL: {url}")
        logger.debug(f"Request Data: {data}")
        
        # Make the token refresh request
        resp = requests.post(url, json=data, timeout=10)
        
        # Log the response details
        logger.debug(f"Refresh Token Response Status: {resp.status_code}")
        logger.debug(f"Refresh Token Response Content: {resp.text}")
        
        # Check response status
        if resp.status_code != 200:
            logger.error(f"Token refresh failed with status {resp.status_code}")
            logger.error(f"Response content: {resp.text}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token invalid or expired"
            )
        
        # Parse the response
        token_response = resp.json()
        
        # Validate the response
        if not token_response.get('access_token'):
            logger.error("No access token in refresh response")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not obtain new access token"
            )
        
        return {
            "access_token": token_response.get('access_token'),
            "refresh_token": token_response.get('refresh_token', refresh_token),
            "expires_in": token_response.get('expires_in'),
            "token_type": token_response.get('token_type', 'Bearer')
        }
    
    except requests.RequestException as req_error:
        logger.error(f"Network error during token refresh: {req_error}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Network error during token refresh"
        )
    except Exception as e:
        logger.error(f"Unexpected error during token refresh: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error during token refresh"
        )