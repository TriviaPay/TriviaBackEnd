"""IAP Router - In-App Purchase verification for Apple and Google."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

import core.config as config
from app.db import get_async_db
from app.dependencies import get_current_user
from app.middleware.rate_limit import RateLimit
from app.models.user import User

from .schemas import AppleVerifyRequest, GoogleVerifyRequest, IapVerifyResponse
from .service import (
    process_apple_notification as service_process_apple_notification,
    process_google_notification as service_process_google_notification,
    verify_apple_purchase as service_verify_apple_purchase,
    verify_google_purchase as service_verify_google_purchase,
)

router = APIRouter(prefix="/iap", tags=["IAP"])

# Rate limits
_verify_rate_limit = RateLimit(prefix="iap_verify", max_requests=10, window_seconds=60)
_webhook_rate_limit = RateLimit(
    prefix="iap_webhook", max_requests=100, window_seconds=60, use_ip_fallback=True
)


@router.post("/apple/verify", response_model=IapVerifyResponse)
async def verify_apple_purchase(
    request: AppleVerifyRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    _rl=Depends(_verify_rate_limit),
):
    return await service_verify_apple_purchase(db, user=user, request=request)


@router.post("/google/verify", response_model=IapVerifyResponse)
async def verify_google_purchase(
    request: GoogleVerifyRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    _rl=Depends(_verify_rate_limit),
):
    return await service_verify_google_purchase(db, user=user, request=request)


@router.post("/apple/webhook")
async def apple_webhook(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _rl=Depends(_webhook_rate_limit),
):
    payload = await request.json()
    signed_payload = payload.get("signedPayload")
    if not signed_payload:
        return {"status": "error", "message": "missing signedPayload"}
    return await service_process_apple_notification(db, signed_payload=signed_payload)


@router.post("/google/webhook")
async def google_webhook(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _rl=Depends(_webhook_rate_limit),
):
    # Verify Pub/Sub push authentication when enabled
    if config.GOOGLE_PUBSUB_VERIFY_ENABLED:
        from app.services.google_pubsub_auth import verify_pubsub_push_token

        authorization = request.headers.get("Authorization")
        await verify_pubsub_push_token(authorization)

    payload = await request.json()
    return await service_process_google_notification(db, payload=payload)
