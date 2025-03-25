import requests
from fastapi import HTTPException, status
from jose import jwt, JWTError
from config import (
    AUTH0_DOMAIN, AUTH0_CLIENT_ID, AUTH0_CLIENT_SECRET,
    API_AUDIENCE, AUTH0_ALGORITHMS, AUTH0_ISSUER
)
import logging

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

        rsa_key = None
        for key in jwks_data["keys"]:
            if key["kid"] == kid:
                rsa_key = {
                    "kty": key["kty"],
                    "kid": key["kid"],
                    "use": key["use"],
                    "n": key["n"],
                    "e": key["e"],
                    "x5c": key["x5c"]
                }
                break

        if not rsa_key:
            logger.error(f"No matching key found for kid: {kid}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: no matching key ID"
            )

        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization

        cert_str = f"-----BEGIN CERTIFICATE-----\n{rsa_key['x5c'][0]}\n-----END CERTIFICATE-----\n"
        public_key = serialization.load_pem_x509_certificate(
            cert_str.encode("utf-8"), default_backend()
        ).public_key()

        payload = jwt.decode(
            access_token,
            public_key,
            algorithms=AUTH0_ALGORITHMS,
            audience=API_AUDIENCE if API_AUDIENCE else None,
            issuer=AUTH0_ISSUER if AUTH0_ISSUER else None,
            options={
                "verify_aud": bool(API_AUDIENCE),
                "verify_iss": bool(AUTH0_ISSUER),
                "require_exp": True,
                "require_iat": True,
            }
        )
        
        # Extensive logging of token claims
        logger.debug("Token Verification Successful")
        logger.debug("Token Claims:")
        for key, value in payload.items():
            logger.debug(f"{key}: {value}")
        
        # Additional validation
        if not payload.get("email"):
            logger.error("Token is missing required 'email' claim")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token is missing required email claim"
            )
        
        return payload

    except jwt.ExpiredSignatureError:
        logger.error("Token has expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired"
        )
    except jwt.InvalidTokenError as e:
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
