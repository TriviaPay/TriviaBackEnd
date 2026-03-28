"""
Stripe Checkout endpoints.
"""

import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
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

router = APIRouter(prefix="/stripe", tags=["stripe"])

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
    product_id: str


class CheckoutSessionResponse(BaseModel):
    checkout_url: str
    session_id: str


# --- Endpoints ---


@router.post("/checkout-session", response_model=CheckoutSessionResponse)
async def create_stripe_checkout_session(
    payload: CheckoutSessionRequest,
    user: User = Depends(require_non_guest),
    db: AsyncSession = Depends(get_async_db),
    _rl=Depends(_checkout_rate_limit),
):
    result = await create_checkout_session(db, user, payload.product_id)
    await db.commit()
    return result


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _rl=Depends(_webhook_rate_limit),
):
    """Stripe webhook — signature verified, no auth."""
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


@router.get("/session-status")
async def get_stripe_session_status(
    session_id: str = Query(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    result = await get_session_status(db, session_id, user.account_id)
    if not result:
        raise HTTPException(status_code=404, detail="Session not found")
    return result
