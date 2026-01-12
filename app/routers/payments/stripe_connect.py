"""Stripe Connect Router - Account onboarding and management."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_db
from app.dependencies import get_current_user
from app.models.user import User

from .schemas import AccountLinkResponse
from .service import (
    create_connect_account_link as service_create_connect_account_link,
    get_publishable_key_public as service_get_publishable_key_public,
    refresh_connect_account_link as service_refresh_connect_account_link,
)

router = APIRouter(prefix="/stripe/connect", tags=["Stripe Connect"])


@router.post("/create-account-link", response_model=AccountLinkResponse)
async def create_account_link_endpoint(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    return_url: str = None,
    refresh_url: str = None,
):
    return await service_create_connect_account_link(
        db, user=user, return_url=return_url, refresh_url=refresh_url
    )


@router.post("/refresh-account-link", response_model=AccountLinkResponse)
async def refresh_account_link_endpoint(
    user: User = Depends(get_current_user),
    return_url: str = None,
    refresh_url: str = None,
):
    return await service_refresh_connect_account_link(
        user=user, return_url=return_url, refresh_url=refresh_url
    )


@router.get("/publishable-key")
async def get_publishable_key_endpoint():
    return service_get_publishable_key_public()
