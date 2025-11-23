"""
Stripe Connect Router - Account onboarding and management
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from app.db import get_async_db
from app.models.user import User
from app.dependencies import get_current_user
from app.services.stripe_service import (
    create_or_get_connect_account,
    create_account_link,
    get_publishable_key,
    StripeError
)

router = APIRouter(prefix="/stripe/connect", tags=["Stripe Connect"])


class AccountLinkResponse(BaseModel):
    url: str
    account_id: str


@router.post("/create-account-link", response_model=AccountLinkResponse)
async def create_account_link_endpoint(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    return_url: str = None,
    refresh_url: str = None
):
    """
    Create or get Stripe Connect account and return onboarding link.
    
    If the user already has a connected account, returns a refresh link.
    """
    try:
        # Ensure account exists
        account_id = await create_or_get_connect_account(user)
        
        # Update user if account was just created
        if not user.stripe_connect_account_id:
            user.stripe_connect_account_id = account_id
            await db.commit()
            await db.refresh(user)
        
        # Create account link
        link_result = await create_account_link(account_id, return_url, refresh_url)
        
        return AccountLinkResponse(
            url=link_result['url'],
            account_id=account_id
        )
        
    except StripeError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/refresh-account-link", response_model=AccountLinkResponse)
async def refresh_account_link_endpoint(
    user: User = Depends(get_current_user),
    return_url: str = None,
    refresh_url: str = None
):
    """
    Refresh account link for existing Stripe Connect account.
    """
    if not user.stripe_connect_account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Stripe Connect account found. Please create one first."
        )
    
    try:
        link_result = await create_account_link(
            user.stripe_connect_account_id,
            return_url,
            refresh_url
        )
        
        return AccountLinkResponse(
            url=link_result['url'],
            account_id=user.stripe_connect_account_id
        )
        
    except StripeError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/publishable-key")
async def get_publishable_key_endpoint():
    """
    Get Stripe publishable key for frontend/testing.
    
    This endpoint is public (no auth required) as publishable keys are safe to expose.
    """
    publishable_key = get_publishable_key()
    if not publishable_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe publishable key not configured"
        )
    return {"publishable_key": publishable_key}

