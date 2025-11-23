"""
Async Dependencies for Authentication
"""
from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db import get_async_db
from app.models.user import User
from auth import validate_descope_jwt


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_async_db)
) -> User:
    """
    Extracts and validates Descope JWT from Authorization header.
    Returns async User model instance.
    """
    auth_header = request.headers.get('authorization') or request.headers.get('Authorization')
    if not auth_header or not auth_header.lower().startswith('bearer '):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization token missing."
        )
    token = auth_header.split(' ', 1)[1].strip()
    user_info = validate_descope_jwt(token)
    
    # Find user in DB by Descope user ID
    stmt = select(User).where(User.descope_user_id == user_info['userId'])
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        # Check if user exists by email (for users created before Descope integration)
        email = user_info['loginIds'][0]
        stmt = select(User).where(User.email == email)
        result = await db.execute(stmt)
        existing_user = result.scalar_one_or_none()
        
        if existing_user:
            # Update existing user with Descope user ID
            existing_user.descope_user_id = user_info['userId']
            await db.commit()
            await db.refresh(existing_user)
            user = existing_user
        else:
            # User doesn't exist
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User profile not found. Please complete profile setup first."
            )
    return user


async def get_admin_user(
    request: Request,
    db: AsyncSession = Depends(get_async_db)
) -> User:
    """Verify user is admin"""
    user = await get_current_user(request, db)
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required for this endpoint"
        )
    return user

