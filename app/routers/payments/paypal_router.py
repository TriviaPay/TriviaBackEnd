"""
PayPal Checkout endpoints.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_db
from app.dependencies import get_current_user, require_non_guest
from app.middleware.rate_limit import RateLimit
from app.models.user import User
from app.services.paypal_service import (
    capture_paypal_order,
    create_paypal_order,
    get_order_status,
    get_subscription_config,
    paypal_client,
    process_paypal_webhook,
    record_subscription_approval,
)
from core.config import PAYPAL_CLIENT_ID, PAYPAL_MODE, PAYPAL_WEBHOOK_ID

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/paypal", tags=["paypal"])

_checkout_rate_limit = RateLimit(
    prefix="paypal_checkout", max_requests=10, window_seconds=60
)
_webhook_rate_limit = RateLimit(
    prefix="paypal_webhook",
    max_requests=100,
    window_seconds=60,
    use_ip_fallback=True,
)


# --- Schemas ---


class CreateOrderRequest(BaseModel):
    product_id: str


class CreateOrderResponse(BaseModel):
    paypal_order_id: str


class CaptureOrderRequest(BaseModel):
    paypal_order_id: str


class CaptureOrderResponse(BaseModel):
    payment_status: str
    fulfillment_status: str
    gems_credited: Optional[int] = None
    asset_granted: bool = False


class SubscriptionConfigResponse(BaseModel):
    paypal_plan_id: str
    product_id: str


class SubscriptionApprovedRequest(BaseModel):
    paypal_subscription_id: str
    product_id: str


class SubscriptionApprovedResponse(BaseModel):
    payment_status: str
    fulfillment_status: str


class OrderStatusResponse(BaseModel):
    payment_status: str
    fulfillment_status: str
    product_id: str
    product_type: str
    gems_credited: Optional[int] = None
    asset_granted: bool = False


class ClientIdResponse(BaseModel):
    client_id: str
    mode: str


# --- Endpoints ---


@router.get("/client-id", response_model=ClientIdResponse)
async def get_paypal_client_id():
    """Public endpoint — returns PayPal client ID for JS SDK."""
    return {"client_id": PAYPAL_CLIENT_ID, "mode": PAYPAL_MODE}


@router.post("/create-order", response_model=CreateOrderResponse)
async def create_order(
    payload: CreateOrderRequest,
    user: User = Depends(require_non_guest),
    db: AsyncSession = Depends(get_async_db),
    _rl=Depends(_checkout_rate_limit),
):
    result = await create_paypal_order(db, user, payload.product_id)
    await db.commit()
    return result


@router.get("/subscription-config", response_model=SubscriptionConfigResponse)
async def subscription_config(
    product_id: str = Query(...),
    user: User = Depends(require_non_guest),
    db: AsyncSession = Depends(get_async_db),
    _rl=Depends(_checkout_rate_limit),
):
    return await get_subscription_config(db, user, product_id)


@router.post("/capture-order", response_model=CaptureOrderResponse)
async def capture_order(
    payload: CaptureOrderRequest,
    user: User = Depends(require_non_guest),
    db: AsyncSession = Depends(get_async_db),
    _rl=Depends(_checkout_rate_limit),
):
    result = await capture_paypal_order(db, user, payload.paypal_order_id)
    await db.commit()
    return result


@router.post("/subscription-approved", response_model=SubscriptionApprovedResponse)
async def subscription_approved(
    payload: SubscriptionApprovedRequest,
    user: User = Depends(require_non_guest),
    db: AsyncSession = Depends(get_async_db),
    _rl=Depends(_checkout_rate_limit),
):
    result = await record_subscription_approval(
        db, user, payload.product_id, payload.paypal_subscription_id
    )
    await db.commit()
    return result


@router.post("/webhook")
async def paypal_webhook(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _rl=Depends(_webhook_rate_limit),
):
    """PayPal webhook — signature verified, no auth."""
    body = await request.body()

    # Collect PayPal signature headers
    paypal_headers = {
        "PAYPAL-AUTH-ALGO": request.headers.get("PAYPAL-AUTH-ALGO", ""),
        "PAYPAL-CERT-URL": request.headers.get("PAYPAL-CERT-URL", ""),
        "PAYPAL-TRANSMISSION-ID": request.headers.get("PAYPAL-TRANSMISSION-ID", ""),
        "PAYPAL-TRANSMISSION-SIG": request.headers.get("PAYPAL-TRANSMISSION-SIG", ""),
        "PAYPAL-TRANSMISSION-TIME": request.headers.get("PAYPAL-TRANSMISSION-TIME", ""),
    }

    if not paypal_headers["PAYPAL-TRANSMISSION-ID"]:
        raise HTTPException(status_code=400, detail="Missing PayPal signature headers")

    # Verify signature
    try:
        is_valid = await paypal_client.verify_webhook_signature(
            headers=paypal_headers,
            body=body,
            webhook_id=PAYPAL_WEBHOOK_ID,
        )
    except Exception as e:
        logger.error("PayPal webhook signature verification failed: %s", e)
        raise HTTPException(status_code=400, detail="Signature verification failed")

    if not is_valid:
        raise HTTPException(status_code=400, detail="Invalid PayPal signature")

    import json

    try:
        event_body = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    await process_paypal_webhook(db, event_body, body)

    # Always return 200 — retry job recovers failures
    return {"status": "ok"}


@router.get("/order-status", response_model=OrderStatusResponse)
async def order_status(
    checkout_id: str = Query(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    result = await get_order_status(db, checkout_id, user.account_id)
    if not result:
        raise HTTPException(status_code=404, detail="Checkout not found")
    return result
