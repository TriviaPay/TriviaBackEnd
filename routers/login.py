from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional

from db import get_db
from models import User, generate_account_id
from auth import verify_access_token

router = APIRouter(prefix="/login", tags=["Login"])

@router.post("/token")
def receive_auth0_tokens(
    access_token: str,
    refresh_token: Optional[str],
    db: Session = Depends(get_db)
):
    """
    1) Verifies the 'access_token' with Auth0
    2) Extracts 'sub' and 'email' from token claims
    3) If user doesn't exist locally, create new record with random 10-digit account_id
    4) Store the refresh_token in DB if provided
    """
    claims = verify_access_token(access_token)
    
    sub = claims.get("sub")
    email = claims.get("email")
    if not sub or not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token is missing 'sub' or 'email' claim"
        )

    # Check if user already exists
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        # Create new user
        user = User(
            account_id=generate_account_id(),
            sub=sub,
            email=email
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    # Update email if changed (rare, but possible)
    if user.email != email:
        user.email = email

    # Store/Update the refresh token
    if refresh_token:
        user.refresh_token = refresh_token

    db.commit()

    return {
        "message": "Local user record updated/created",
        "account_id": user.account_id,
        "sub": user.sub
    }
