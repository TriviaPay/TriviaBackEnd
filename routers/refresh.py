from fastapi import APIRouter, Depends, HTTPException, status, Header
from typing import Optional
from sqlalchemy.orm import Session

from db import get_db
from auth import verify_access_token, refresh_auth0_token
from models import User

router = APIRouter(prefix="/auth", tags=["Refresh"])

@router.post("/refresh")
def refresh_access_token(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """
    Client sends old or nearly-expired 'access_token' in Authorization header.
    We decode to find 'sub', fetch user's refresh_token, call Auth0 for a new access_token.
    Return the new access_token (and store new refresh_token if provided).
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="No Authorization header")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid auth scheme")

    # Attempt to decode the old token
    claims = verify_access_token(token)
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Token has no 'sub' claim")

    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found in DB")

    if not user.refresh_token:
        raise HTTPException(
            status_code=400,
            detail="No refresh token stored. Re-login with Auth0?"
        )

    new_tokens = refresh_auth0_token(user.refresh_token)
    new_access_token = new_tokens.get("access_token")
    new_refresh_token = new_tokens.get("refresh_token")

    if not new_access_token:
        raise HTTPException(status_code=401, detail="Refresh token invalid/expired")

    # Update DB if Auth0 returns a new refresh_token
    if new_refresh_token:
        user.refresh_token = new_refresh_token
        db.commit()

    return {"access_token": new_access_token}
