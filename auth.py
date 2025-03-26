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
    
    try:
        userinfo_url = f"https://{AUTH0_DOMAIN}/userinfo"
        headers = {"Authorization": f"Bearer {access_token}"}
        
        # Log the request details for debugging
        logger.debug(f"Userinfo Request URL: {userinfo_url}")
        logger.debug(f"Authorization Header: {headers}")
        
        response = requests.get(userinfo_url, headers=headers)
        
        logger.debug(f"Userinfo Response Status: {response.status_code}")
        logger.debug(f"Userinfo Response Content: {response.text}")
        
        if response.status_code != 200:
            logger.error(f"Userinfo request failed with status {response.status_code}")
            logger.error(f"Response content: {response.text}")
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
    
    except Exception as e:
        logger.error(f"Error retrieving userinfo: {str(e)}")
        return None

def verify_access_token(access_token: str) -> dict:
    """
    Verify the access token's signature/claims using Auth0's public JWKS or local secret.
    Returns the decoded payload if valid, else raises 401.
    """
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.DEBUG)

    try:
        # Log the raw token for debugging
        logger.debug(f"Received token: {access_token}")

        # First, try to get the unverified header
        try:
            unverified_header = jwt.get_unverified_header(access_token)
        except Exception as header_error:
            logger.error(f"Error decoding token headers: {header_error}")
            logger.error(f"Full token: {access_token}")
            raise JWTError(f"Error decoding token headers: {header_error}")

        logger.debug(f"Unverified Token Header: {unverified_header}")

        # Check if this is a local test token (HS256 algorithm)
        if unverified_header.get('alg') == 'HS256':
            logger.debug("Detected local test token, using client secret for verification")
            try:
                payload = jwt.decode(
                    access_token, 
                    AUTH0_CLIENT_SECRET, 
                    algorithms=['HS256'],
                    options={
                        "verify_aud": False,
                        "verify_iss": False,
                        "require_exp": True,
                        "require_iat": True,
                    }
                )
                return payload
            except Exception as decode_error:
                logger.error(f"Error decoding local test token: {decode_error}")
                raise JWTError(f"Invalid local test token: {decode_error}")

        # For Auth0 tokens, proceed with JWKS verification
        try:
            jwks_url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
            jwks_response = requests.get(jwks_url)
            jwks_data = jwks_response.json()
        except Exception as jwks_error:
            logger.error(f"Error fetching JWKS: {jwks_error}")
            raise JWTError(f"Could not fetch JWKS: {jwks_error}")

        kid = unverified_header.get("kid")
        if not kid:
            logger.error("No key ID (kid) found in token header")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: no key ID found"
            )

        # Find the right key
        rsa_key = None
        for key in jwks_data["keys"]:
            if key["kid"] == kid:
                rsa_key = key
                break

        if not rsa_key:
            logger.error(f"No matching key found for kid: {kid}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: no matching key ID"
            )

        # Decode the token using the JWKS
        try:
            payload = jwt.decode(
                access_token,
                jwks_data,
                algorithms=['RS256'],
                audience=API_AUDIENCE,
                issuer=f"https://{AUTH0_DOMAIN}/"
            )
            
            # If no email in payload, try to get from userinfo
            if not payload.get('email'):
                logger.info("No email in token, attempting to retrieve from userinfo")
                email = get_email_from_userinfo(access_token)
                if email:
                    payload['email'] = email
                else:
                    logger.error("Could not retrieve email from userinfo")
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Could not retrieve user email"
                    )
            
            return payload
        except jwt.JWTError as decode_error:
            logger.error(f"Token decode error: {decode_error}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {str(decode_error)}"
            )

    except jwt.ExpiredSignatureError:
        logger.error("Token has expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired"
        )
    except (jwt.JWTError, JWTError) as e:
        logger.error(f"Invalid token: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}"
        )

def refresh_auth0_token(refresh_token: str) -> dict:
    """
    Exchange the stored refresh token for a new access token via Auth0.
    """
    url = f"https://{AUTH0_DOMAIN}/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": AUTH0_CLIENT_ID,
        "client_secret": AUTH0_CLIENT_SECRET,
        "refresh_token": refresh_token
    }
    resp = requests.post(url, json=data, timeout=10)
    if resp.status_code != 200:
        print(resp.text)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token invalid or expired"
        )
    return resp.json()
