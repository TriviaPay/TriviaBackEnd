"""
Stripe Checkout endpoints.
"""

import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_db
from app.dependencies import get_current_user, require_non_guest
from app.middleware.rate_limit import RateLimit
from app.models.user import User
from app.services.stripe_service import (
    create_checkout_session,
    get_session_status,
    process_webhook_event,
)
from core.config import STRIPE_WEBHOOK_SECRET

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stripe", tags=["Stripe"])

_checkout_rate_limit = RateLimit(
    prefix="stripe_checkout", max_requests=10, window_seconds=60
)
_webhook_rate_limit = RateLimit(
    prefix="stripe_webhook",
    max_requests=100,
    window_seconds=60,
    use_ip_fallback=True,
)


# --- Schemas ---


class CheckoutSessionRequest(BaseModel):
    """Request to create a Stripe Checkout session."""

    product_id: str = Field(
        ...,
        description=(
            "Product ID to purchase. Must match a configured product "
            "(gem package, avatar, frame, or subscription). "
            "Examples: 'GP001', 'AV001', 'FR001', 'SUB_BRONZE_MONTHLY'."
        ),
        example="GP001",
    )


class CheckoutSessionResponse(BaseModel):
    """Response containing the Stripe Checkout URL to redirect the user to."""

    checkout_url: str = Field(
        ...,
        description=(
            "Full Stripe Checkout URL. Redirect the user to this URL "
            "to complete payment. The URL expires after 24 hours."
        ),
        example="https://checkout.stripe.com/c/pay/cs_test_...",
    )
    session_id: str = Field(
        ...,
        description=(
            "Stripe Checkout Session ID. Use this to poll session "
            "status via GET /stripe/session-status."
        ),
        example="cs_test_a1b2c3d4e5f6",
    )


# --- Endpoints ---


@router.post(
    "/checkout-session",
    response_model=CheckoutSessionResponse,
    summary="Create a Stripe Checkout session",
    description=(
        "Creates a new Stripe Checkout session for the given product. "
        "Returns a `checkout_url` that the client should redirect or "
        "open in a browser.\n\n"
        "**Supported product types:**\n"
        "- Gem packages (consumable) — credits gems to wallet on payment\n"
        "- Avatars / Frames (non-consumable) — grants the cosmetic item\n"
        "- Subscriptions (recurring) — activates bronze/silver mode access\n\n"
        "**Guest users are not allowed** — requires a registered account.\n\n"
        "**Rate limit:** 10 requests / 60 seconds per user."
    ),
    responses={
        200: {"description": "Checkout session created successfully"},
        400: {"description": "Unknown product_id or product not available for Stripe"},
        401: {"description": "Not authenticated"},
        403: {"description": "Guest users cannot make purchases"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def create_stripe_checkout_session(
    payload: CheckoutSessionRequest,
    user: User = Depends(require_non_guest),
    db: AsyncSession = Depends(get_async_db),
    _rl=Depends(_checkout_rate_limit),
):
    result = await create_checkout_session(db, user, payload.product_id)
    await db.commit()
    return result


@router.post(
    "/webhook",
    summary="Stripe webhook receiver",
    description=(
        "Receives Stripe webhook events. Signature is verified using "
        "the `Stripe-Signature` header and the configured webhook secret.\n\n"
        "**Handled events:**\n"
        "- `checkout.session.completed` — fulfills the purchase (credits wallet, grants item, or activates subscription)\n"
        "- `invoice.paid` — processes subscription renewals\n"
        "- `customer.subscription.deleted` — deactivates expired subscriptions\n"
        "- `charge.refunded` — reverses wallet credits\n\n"
        "**No user authentication** — Stripe signs the payload.\n\n"
        "Configure this URL in Stripe Dashboard > Developers > Webhooks.\n\n"
        "**Rate limit:** 100 requests / 60 seconds per IP."
    ),
    responses={
        200: {"description": "Webhook processed successfully"},
        400: {"description": "Missing Stripe-Signature header or invalid signature"},
    },
)
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _rl=Depends(_webhook_rate_limit),
):
    body = await request.body()
    sig = request.headers.get("Stripe-Signature")
    if not sig:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")

    try:
        event = stripe.Webhook.construct_event(
            body, sig, STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        logger.error("Webhook signature verification failed: %s", e)
        raise HTTPException(status_code=400, detail="Invalid payload")

    await process_webhook_event(db, event)

    # Always return 200 — retry job recovers failures
    return {"status": "ok"}


@router.get(
    "/session-status",
    summary="Check Stripe Checkout session status",
    description=(
        "Poll the status of a Stripe Checkout session after the user "
        "returns from the Checkout page. Use this to confirm whether "
        "payment succeeded before showing a success screen.\n\n"
        "**Typical flow:**\n"
        "1. Client creates session via `POST /stripe/checkout-session`\n"
        "2. User completes payment on Stripe's hosted page\n"
        "3. User is redirected back to the app\n"
        "4. Client polls this endpoint with the `session_id`\n\n"
        "**Possible statuses:** `pending`, `paid`, `failed`, `expired`"
    ),
    responses={
        200: {
            "description": "Session status retrieved",
            "content": {
                "application/json": {
                    "example": {
                        "payment_status": "paid",
                        "fulfillment_status": "fulfilled",
                        "product_id": "GP001",
                        "product_type": "gem_package",
                        "gems_credited": 100,
                        "asset_granted": False,
                    }
                }
            },
        },
        401: {"description": "Not authenticated"},
        404: {"description": "Session not found or does not belong to this user"},
    },
)
async def get_stripe_session_status(
    session_id: str = Query(
        ...,
        description=(
            "The Stripe Checkout Session ID returned from "
            "`POST /stripe/checkout-session`."
        ),
        example="cs_test_a1b2c3d4e5f6",
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    result = await get_session_status(db, session_id, user.account_id)
    if not result:
        raise HTTPException(status_code=404, detail="Session not found")
    return result
