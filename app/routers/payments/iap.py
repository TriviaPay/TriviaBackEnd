"""IAP Router - In-App Purchase verification for Apple and Google."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_db
from app.dependencies import get_current_user
from app.models.user import User

from .schemas import AppleVerifyRequest, GoogleVerifyRequest, IapVerifyResponse
from .service import (
    verify_apple_purchase as service_verify_apple_purchase,
    verify_google_purchase as service_verify_google_purchase,
)

router = APIRouter(prefix="/iap", tags=["IAP"])


@router.post("/apple/verify", response_model=IapVerifyResponse)
async def verify_apple_purchase(
    request: AppleVerifyRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    return await service_verify_apple_purchase(db, user=user, request=request)


@router.post("/google/verify", response_model=IapVerifyResponse)
async def verify_google_purchase(
    request: GoogleVerifyRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    return await service_verify_google_purchase(db, user=user, request=request)


@router.post("/apple/webhook")
async def apple_webhook():
    return {"status": "not_implemented", "message": "Apple webhook not yet implemented"}


@router.post("/google/webhook")
async def google_webhook():
    return {"status": "not_implemented", "message": "Google webhook not yet implemented"}
