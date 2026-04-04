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


@router.post(
    "/apple/verify",
    response_model=IapVerifyResponse,
    summary="Verify an Apple StoreKit 2 purchase",
    description=(
        "Verifies an Apple in-app purchase using the StoreKit 2 "
        "`signedTransactionInfo` JWS. On success the server:\n\n"
        "1. Decodes and validates the JWS signature against Apple's root CA\n"
        "2. Records an `iap_receipt` (idempotent — duplicate transactions return `already_processed: true`)\n"
        "3. Credits the user's wallet (for consumables/gem packages) or activates a subscription\n\n"
        "**Rate limit:** 10 requests / 60 seconds per user."
    ),
    responses={
        200: {"description": "Purchase verified and processed"},
        400: {"description": "Invalid JWS, unknown product_id, or environment mismatch"},
        401: {"description": "Not authenticated"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def verify_apple_purchase(
    request: AppleVerifyRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    _rl=Depends(_verify_rate_limit),
):
    return await service_verify_apple_purchase(db, user=user, request=request)


@router.post(
    "/google/verify",
    response_model=IapVerifyResponse,
    summary="Verify a Google Play purchase",
    description=(
        "Verifies a Google Play purchase using the `purchase_token`. "
        "On success the server:\n\n"
        "1. Queries Google Play Developer API to validate the purchase\n"
        "2. Records an `iap_receipt` (idempotent — duplicate tokens return `already_processed: true`)\n"
        "3. Credits the user's wallet (for consumables/gem packages) or activates a subscription\n\n"
        "**Rate limit:** 10 requests / 60 seconds per user."
    ),
    responses={
        200: {"description": "Purchase verified and processed"},
        400: {"description": "Invalid purchase_token, unknown product_id, or missing package_name"},
        401: {"description": "Not authenticated"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def verify_google_purchase(
    request: GoogleVerifyRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    _rl=Depends(_verify_rate_limit),
):
    return await service_verify_google_purchase(db, user=user, request=request)


@router.post(
    "/apple/webhook",
    summary="Apple App Store Server Notification webhook",
    description=(
        "Receives Apple App Store Server Notifications (V2). "
        "Handles: `DID_RENEW`, `EXPIRED`, `REVOKE`, `REFUND`, "
        "`GRACE_PERIOD_EXPIRED`, `DID_FAIL_TO_RENEW`, "
        "`DID_CHANGE_RENEWAL_STATUS`.\n\n"
        "**No authentication required** — Apple signs the payload. "
        "Configure this URL in App Store Connect > App > "
        "App Store Server Notifications.\n\n"
        "**Rate limit:** 100 requests / 60 seconds per IP."
    ),
    responses={
        200: {"description": "Notification processed or already processed"},
        400: {"description": "Missing signedPayload in request body"},
    },
)
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


@router.post(
    "/google/webhook",
    summary="Google Play Real-time Developer Notification webhook",
    description=(
        "Receives Google Play RTDN via Cloud Pub/Sub push. "
        "Handles subscription state changes (renewal, expiry, "
        "revocation, pause, hold) and one-time purchase refunds.\n\n"
        "**No user authentication** — authenticates via Pub/Sub push "
        "token when `GOOGLE_PUBSUB_VERIFY_ENABLED=true`.\n\n"
        "Configure this URL as the Pub/Sub push endpoint for your "
        "Google Play RTDN topic.\n\n"
        "**Rate limit:** 100 requests / 60 seconds per IP."
    ),
    responses={
        200: {"description": "Notification processed or already processed"},
        401: {"description": "Invalid Pub/Sub push token (when verification enabled)"},
    },
)
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
