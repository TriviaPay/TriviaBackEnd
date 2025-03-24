from fastapi import Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from typing import Optional
from db import get_db
from auth import verify_access_token
from models import User

async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> User:
    """
    - Expects an Authorization header like "Bearer <access_token>"
    - Verifies the token signature with Auth0
    - Extracts the "sub" claim
    - Finds the user in local DB
    - Returns the user object if found
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="No Authorization header")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid auth scheme")

    claims = verify_access_token(token)
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Token has no 'sub' claim")

    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        # If no local record, user hasn't called /login/token yet
        raise HTTPException(status_code=401, detail="User not found in DB")

    return user
