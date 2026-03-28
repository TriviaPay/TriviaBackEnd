import logging
import random
import string
import time
import uuid as uuid_mod
from datetime import datetime
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.db import get_db
from core.security import validate_descope_jwt
from models import AdminUser, User

logger = logging.getLogger(__name__)


def get_current_user(request: Request, db=Depends(get_db)):
    """
    Extracts and validates Descope JWT from Authorization header. Returns user info dict.
    Users must be created through the /bind-password endpoint, not automatically here.
    """
    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization token missing.",
        )
    token = auth_header.split(" ", 1)[1].strip()
    user_info = validate_descope_jwt(token)

    # Find user in DB by Descope user ID
    user = db.query(User).filter(User.descope_user_id == user_info["userId"]).first()
    if not user:
        # Check if user exists by email (for users created before Descope integration)
        email = user_info["loginIds"][0]
        existing_user = db.query(User).filter(User.email == email).first()
        if existing_user:
            # Update existing user with Descope user ID but don't change username
            existing_user.descope_user_id = user_info["userId"]
            db.commit()
            db.refresh(existing_user)
            user = existing_user
        else:
            # User doesn't exist - they need to complete profile binding
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User profile not found. Please complete profile setup first.",
            )

    # Set user_id on request.state for LastActiveMiddleware
    request.state.user_id = user.account_id
    return user


def _generate_guest_username() -> str:
    """Generate a random guest username like Guest_a8f3k2m9 (14 chars)."""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"Guest_{suffix}"


def _check_guest_creation_rate_limit(request: Request):
    """IP-based rate limit for guest creation only. Uses Redis with fallback."""
    from core.config import GUEST_CREATION_RATE_LIMIT_MAX, GUEST_CREATION_RATE_LIMIT_WINDOW

    client_ip = request.client.host if request.client else "unknown"
    key = f"guest_create:{client_ip}"

    try:
        import redis
        from core.config import REDIS_URL

        r = redis.from_url(REDIS_URL, decode_responses=True)
        current = r.incr(key)
        if current == 1:
            r.expire(key, GUEST_CREATION_RATE_LIMIT_WINDOW)
        if current > GUEST_CREATION_RATE_LIMIT_MAX:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many guest accounts created. Please try again later.",
            )
    except HTTPException:
        raise
    except Exception:
        # Redis unavailable — fail open
        logger.debug("Guest rate limit check failed (Redis unavailable)", exc_info=True)


def get_current_user_or_guest(request: Request, db=Depends(get_db)):
    """
    Returns an authenticated User if a Bearer token is present and valid.
    If no Bearer token, looks for X-Device-UUID header and returns/creates a guest User.
    Raises 401 if neither is provided.
    """
    # Try standard auth first
    auth_header = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    if auth_header and auth_header.lower().startswith("bearer "):
        return get_current_user(request, db)

    # Guest path
    from core.config import GUEST_DEFAULT_GEMS, GUEST_MODE_ENABLED

    if not GUEST_MODE_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Guest mode is not enabled.",
        )

    device_uuid = request.headers.get("X-Device-UUID")
    if not device_uuid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization token or X-Device-UUID required.",
        )

    # Validate and normalize UUID
    try:
        device_uuid = str(uuid_mod.UUID(device_uuid))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid X-Device-UUID format.",
        )

    # Look up existing guest
    guest = (
        db.query(User)
        .filter(
            User.guest_device_uuid == device_uuid,
            User.is_guest.is_(True),
            User.guest_device_uuid.isnot(None),
        )
        .first()
    )

    if guest:
        request.state.user_id = guest.account_id
        return guest

    # Create new guest — rate limit check first
    _check_guest_creation_rate_limit(request)

    # Retry loop for username collision
    last_error = None
    for attempt in range(3):
        username = _generate_guest_username()
        email = f"guest_{device_uuid}@guest.triviapay.local"

        guest = User(
            email=email,
            username=username,
            is_guest=True,
            guest_device_uuid=device_uuid,
            gems=GUEST_DEFAULT_GEMS,
            last_active_at=datetime.utcnow(),
            country="Unknown",
        )
        db.add(guest)
        try:
            db.commit()
            db.refresh(guest)
            request.state.user_id = guest.account_id
            return guest
        except IntegrityError as e:
            db.rollback()
            last_error = e

            # Check which constraint was violated via the DB driver's diagnostic info
            constraint_name = getattr(
                getattr(e.orig, "diag", None), "constraint_name", None
            ) or ""
            is_device_or_email_conflict = (
                "guest_device_uuid" in constraint_name
                or "email" in constraint_name
                or "users_guest_device_uuid_key" in constraint_name
                or "users_email_key" in constraint_name
            )

            # If it's a guest_device_uuid or email conflict, another request won the race
            if is_device_or_email_conflict:
                # Retry loop to handle competing transaction not yet committed
                for _retry in range(2):
                    existing = (
                        db.query(User)
                        .filter(
                            User.guest_device_uuid == device_uuid,
                            User.is_guest.is_(True),
                        )
                        .first()
                    )
                    if existing:
                        request.state.user_id = existing.account_id
                        return existing
                    time.sleep(0.05)
                    db.rollback()

                # If still not found after retries, raise
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create guest account. Please try again.",
                )
            # Username collision — retry with new random username
            continue

    # Exhausted username retries
    logger.error("Failed to create guest after 3 username attempts: %s", last_error)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Failed to create guest account. Please try again.",
    )


def get_optional_user(request: Request, db=Depends(get_db)):
    """Returns a User (authenticated or guest) if credentials exist, or None."""
    try:
        return get_current_user_or_guest(request, db)
    except HTTPException:
        return None


def require_non_guest(
    request: Request, db=Depends(get_db)
):
    """Returns user if authenticated and not a guest. Raises 403 for guests."""
    user = get_current_user_or_guest(request, db)
    if user.is_guest:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please sign up to use this feature.",
        )
    return user


def validate_jwt_dependency(request: Request):
    auth_header = request.headers.get("Authorization") or request.headers.get(
        "authorization"
    )
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token"
        )
    token = auth_header.split(" ", 1)[1].strip()
    return validate_descope_jwt(token)


def get_current_user_simple(claims: dict = Depends(validate_jwt_dependency)):
    return claims


def is_admin_user(db: Session, user_id: int) -> bool:
    return (
        db.query(AdminUser).filter(AdminUser.user_id == user_id).first()
        is not None
    )


def verify_admin(db: Session, user: User):
    if not is_admin_user(db, user.account_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required for this endpoint",
        )


def get_admin_user(request: Request, db: Session = Depends(get_db)):
    """Verify user is admin using admin_users table."""
    user = get_current_user(request, db)
    verify_admin(db, user)
    return user
