import requests
from fastapi import HTTPException, status
from jose import jwt, JWTError
from config import (
    AUTH0_DOMAIN, AUTH0_CLIENT_ID, AUTH0_CLIENT_SECRET,
    API_AUDIENCE, AUTH0_ALGORITHMS, AUTH0_ISSUER
)

def verify_access_token(access_token: str) -> dict:
    """
    Verify the access token's signature/claims using Auth0's public JWKS.
    Returns the decoded payload if valid, else raises 401.
    """
    jwks_url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
    jwks_data = requests.get(jwks_url).json()

    unverified_header = jwt.get_unverified_header(access_token)
    kid = unverified_header.get("kid")
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: no matching kid"
        )

    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization

    cert_str = f"-----BEGIN CERTIFICATE-----\n{rsa_key['x5c'][0]}\n-----END CERTIFICATE-----\n"
    public_key = serialization.load_pem_x509_certificate(
        cert_str.encode("utf-8"), default_backend()
    ).public_key()

    try:
        payload = jwt.decode(
            access_token,
            public_key,
            algorithms=AUTH0_ALGORITHMS,
            audience=API_AUDIENCE if API_AUDIENCE else None,
            issuer=AUTH0_ISSUER if AUTH0_ISSUER else None
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token verification error: {str(e)}"
        )

    return payload

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
