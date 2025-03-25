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

def base64url_decode(input_str):
    """
    Decode base64url encoded string
    """
    # Add padding if needed
    input_str += '=' * (4 - (len(input_str) % 4))
    return base64.urlsafe_b64decode(input_str)

def get_email_from_userinfo(access_token: str) -> str:
    """
    Retrieve email from Auth0 userinfo endpoint
    
    Args:
        access_token (str): JWT access token
    
    Returns:
        str: User's email address
    """
    logger = logging.getLogger(__name__)
    
    try:
        userinfo_url = f"https://{AUTH0_DOMAIN}/userinfo"
        headers = {"Authorization": f"Bearer {access_token}"}
        
        response = requests.get(userinfo_url, headers=headers)
        
        if response.status_code != 200:
            logger.error(f"Userinfo request failed with status {response.status_code}")
            logger.error(f"Response content: {response.text}")
            return None
        
        userinfo = response.json()
        logger.debug(f"Userinfo retrieved: {userinfo}")
        
        return userinfo.get('email')
    
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
        # First, try to get the unverified header
        unverified_header = jwt.get_unverified_header(access_token)
        logger.debug(f"Unverified Token Header: {unverified_header}")

        # Check if this is a local test token (HS256 algorithm)
        if unverified_header.get('alg') == 'HS256':
            logger.debug("Detected local test token, using client secret for verification")
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

        # For Auth0 tokens, proceed with JWKS verification
        jwks_url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
        jwks_data = requests.get(jwks_url).json()

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

        # Convert JWKS key to a public key
        n = base64url_decode(rsa_key['n'])
        e = base64url_decode(rsa_key['e'])

        public_key = serialization.load_der_public_key(
            rsa.RSAPublicNumbers(
                int.from_bytes(e, byteorder='big'),
                int.from_bytes(n, byteorder='big')
            ).public_key().public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )

        # Manual token verification
        parts = access_token.split('.')
        if len(parts) != 3:
            raise JWTError("Invalid token format")

        header, payload, signature = parts
        signing_input = f"{header}.{payload}"

        try:
            # Verify signature
            public_key.verify(
                base64url_decode(signature),
                signing_input.encode('utf-8'),
                padding.PKCS1v15(),
                hashes.SHA256()
            )
        except InvalidSignature:
            raise JWTError("Invalid token signature")

        # Decode payload
        decoded_payload = json.loads(base64url_decode(payload).decode('utf-8'))

        # Validate claims
        current_time = int(datetime.datetime.utcnow().timestamp())
        if decoded_payload.get('exp', 0) < current_time:
            raise JWTError("Token has expired")

        if API_AUDIENCE:
            # Check audience
            aud = decoded_payload.get('aud', [])
            if isinstance(aud, str):
                aud = [aud]
            if not any(aud_item.startswith(API_AUDIENCE) for aud_item in aud):
                raise JWTError("Invalid audience")

        if AUTH0_ISSUER and decoded_payload.get('iss') != AUTH0_ISSUER:
            raise JWTError("Invalid issuer")

        # Additional validation
        # If no email in token, try to fetch from userinfo
        if not decoded_payload.get("email"):
            logger.info("No email in token, attempting to retrieve from userinfo")
            email = get_email_from_userinfo(access_token)
            if email:
                decoded_payload['email'] = email
            else:
                logger.error("Could not retrieve email from userinfo")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Could not retrieve user email"
                )
        
        return decoded_payload

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
