"""
PayPal Checkout endpoints.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
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

router = APIRouter(prefix="/paypal", tags=["PayPal"])

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
    """Request to create a PayPal order for a one-time purchase."""

    product_id: str = Field(
        ...,
        description=(
            "Product ID to purchase. Must match a configured product "
            "(gem package, avatar, frame). "
            "Examples: 'GP001', 'AV001', 'FR001'."
        ),
        example="GP001",
    )


class CreateOrderResponse(BaseModel):
    """Response containing the PayPal order ID for approval."""

    paypal_order_id: str = Field(
        ...,
        description=(
            "PayPal order ID. Use this with the PayPal JS SDK's "
            "`actions.order.capture()` or redirect the user to "
            "PayPal for approval."
        ),
        example="5O190127TN364715T",
    )


class CaptureOrderRequest(BaseModel):
    """Request to capture (finalize) a PayPal order after user approval."""

    paypal_order_id: str = Field(
        ...,
        description=(
            "The PayPal order ID returned from `POST /paypal/create-order`. "
            "The order must be in 'APPROVED' state (user completed PayPal approval)."
        ),
        example="5O190127TN364715T",
    )


class CaptureOrderResponse(BaseModel):
    """Response after capturing a PayPal order."""

    payment_status: str = Field(
        ...,
        description="Payment status: 'captured', 'pending', 'denied', 'failed'",
        example="captured",
    )
    fulfillment_status: str = Field(
        ...,
        description="Fulfillment status: 'fulfilled' or 'unfulfilled'",
        example="fulfilled",
    )
    gems_credited: Optional[int] = Field(
        None,
        description="Number of gems credited (for gem packages)",
        example=100,
    )
    asset_granted: bool = Field(
        False,
        description="True if a non-consumable asset (avatar/frame) was granted",
        example=False,
    )


class SubscriptionConfigResponse(BaseModel):
    """PayPal subscription plan configuration for the JS SDK."""

    paypal_plan_id: str = Field(
        ...,
        description=(
            "PayPal billing plan ID. Pass this to the PayPal JS SDK's "
            "`createSubscription()` callback."
        ),
        example="P-5ML4271244454362WXNWU5NQ",
    )
    product_id: str = Field(
        ...,
        description="The product ID that maps to this plan",
        example="SUB_BRONZE_MONTHLY",
    )


class SubscriptionApprovedRequest(BaseModel):
    """Notify the backend that the user approved a PayPal subscription."""

    paypal_subscription_id: str = Field(
        ...,
        description=(
            "PayPal subscription ID returned after user approval. "
            "The PayPal JS SDK provides this in the `onApprove` callback."
        ),
        example="I-BW452GLLEP1G",
    )
    product_id: str = Field(
        ...,
        description="Product ID of the subscription plan",
        example="SUB_BRONZE_MONTHLY",
    )


class SubscriptionApprovedResponse(BaseModel):
    """Response after recording a subscription approval."""

    payment_status: str = Field(
        ...,
        description="Subscription payment status: 'active' or 'pending'",
        example="active",
    )
    fulfillment_status: str = Field(
        ...,
        description="Fulfillment status: 'fulfilled' once subscription is activated",
        example="fulfilled",
    )


class OrderStatusResponse(BaseModel):
    """Status of a PayPal checkout (order or subscription)."""

    payment_status: str = Field(
        ...,
        description=(
            "Current payment status. For orders: 'created', 'approved', "
            "'captured', 'denied', 'failed'. For subscriptions: "
            "'created', 'approved', 'active', 'failed'."
        ),
        example="captured",
    )
    fulfillment_status: str = Field(
        ...,
        description="Fulfillment status: 'unfulfilled', 'fulfilled', 'refunded'",
        example="fulfilled",
    )
    product_id: str = Field(
        ..., description="Product ID of the purchase", example="GP001"
    )
    product_type: str = Field(
        ...,
        description="Product type: 'gem_package', 'consumable', 'non_consumable', 'subscription'",
        example="gem_package",
    )
    gems_credited: Optional[int] = Field(
        None, description="Gems credited (for gem packages)", example=100
    )
    asset_granted: bool = Field(
        False, description="Whether a non-consumable asset was granted"
    )


class ClientIdResponse(BaseModel):
    """PayPal client configuration for the JS SDK."""

    client_id: str = Field(
        ...,
        description="PayPal client ID for the JS SDK initialization",
        example="AaBbCcDdEeFfGgHhIiJjKkLlMmNnOo",
    )
    mode: str = Field(
        ...,
        description="PayPal environment: 'sandbox' or 'live'",
        example="sandbox",
    )


# --- Endpoints ---


@router.get(
    "/client-id",
    response_model=ClientIdResponse,
    summary="Get PayPal client ID",
    description=(
        "Returns the PayPal client ID and environment mode for "
        "initializing the PayPal JS SDK on the frontend.\n\n"
        "**No authentication required** — this is a public endpoint."
    ),
    responses={
        200: {"description": "PayPal client configuration"},
    },
)
async def get_paypal_client_id():
    return {"client_id": PAYPAL_CLIENT_ID, "mode": PAYPAL_MODE}


@router.post(
    "/create-order",
    response_model=CreateOrderResponse,
    summary="Create a PayPal order",
    description=(
        "Creates a PayPal order for a one-time purchase (gem package, "
        "avatar, or frame). Returns a `paypal_order_id` for the "
        "PayPal JS SDK to render the payment buttons.\n\n"
        "**Flow:**\n"
        "1. Client calls this endpoint with the product_id\n"
        "2. Server creates a PayPal order via the PayPal API\n"
        "3. Client uses the returned `paypal_order_id` with the PayPal JS SDK\n"
        "4. User approves payment on PayPal\n"
        "5. Client calls `POST /paypal/capture-order` to finalize\n\n"
        "**Guest users are not allowed.**\n\n"
        "**Rate limit:** 10 requests / 60 seconds per user."
    ),
    responses={
        200: {"description": "PayPal order created"},
        400: {"description": "Unknown product_id or product not available"},
        401: {"description": "Not authenticated"},
        403: {"description": "Guest users cannot make purchases"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def create_order(
    payload: CreateOrderRequest,
    user: User = Depends(require_non_guest),
    db: AsyncSession = Depends(get_async_db),
    _rl=Depends(_checkout_rate_limit),
):
    result = await create_paypal_order(db, user, payload.product_id)
    await db.commit()
    return result


@router.get(
    "/subscription-config",
    response_model=SubscriptionConfigResponse,
    summary="Get PayPal subscription plan config",
    description=(
        "Returns the PayPal billing plan ID for a subscription product. "
        "The client uses this plan ID to render the PayPal subscription "
        "button via the JS SDK.\n\n"
        "**Guest users are not allowed.**\n\n"
        "**Rate limit:** 10 requests / 60 seconds per user."
    ),
    responses={
        200: {"description": "Subscription plan configuration"},
        400: {"description": "Unknown product_id or not a subscription product"},
        401: {"description": "Not authenticated"},
        403: {"description": "Guest users cannot subscribe"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def subscription_config(
    product_id: str = Query(
        ...,
        description=(
            "Subscription product ID. "
            "Examples: 'SUB_BRONZE_MONTHLY', 'SUB_SILVER_MONTHLY'."
        ),
        example="SUB_BRONZE_MONTHLY",
    ),
    user: User = Depends(require_non_guest),
    db: AsyncSession = Depends(get_async_db),
    _rl=Depends(_checkout_rate_limit),
):
    return await get_subscription_config(db, user, product_id)


@router.post(
    "/capture-order",
    response_model=CaptureOrderResponse,
    summary="Capture (finalize) a PayPal order",
    description=(
        "Captures an approved PayPal order to complete the payment. "
        "Call this after the user approves payment on PayPal.\n\n"
        "On successful capture the server fulfills the purchase: "
        "credits gems, grants cosmetic items, etc.\n\n"
        "**Guest users are not allowed.**\n\n"
        "**Rate limit:** 10 requests / 60 seconds per user."
    ),
    responses={
        200: {"description": "Order captured and fulfilled"},
        400: {"description": "Order not in APPROVED state or capture failed"},
        401: {"description": "Not authenticated"},
        403: {"description": "Guest users cannot make purchases"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def capture_order(
    payload: CaptureOrderRequest,
    user: User = Depends(require_non_guest),
    db: AsyncSession = Depends(get_async_db),
    _rl=Depends(_checkout_rate_limit),
):
    result = await capture_paypal_order(db, user, payload.paypal_order_id)
    await db.commit()
    return result


@router.post(
    "/subscription-approved",
    response_model=SubscriptionApprovedResponse,
    summary="Record PayPal subscription approval",
    description=(
        "Notifies the backend that the user approved a PayPal subscription. "
        "The server records the subscription ID and activates the user's "
        "subscription (e.g., grants bronze or silver mode access).\n\n"
        "**Flow:**\n"
        "1. Client gets plan ID via `GET /paypal/subscription-config`\n"
        "2. PayPal JS SDK renders subscription button\n"
        "3. User approves on PayPal\n"
        "4. Client receives `subscriptionID` in `onApprove` callback\n"
        "5. Client calls this endpoint with the subscription ID\n\n"
        "**Guest users are not allowed.**\n\n"
        "**Rate limit:** 10 requests / 60 seconds per user."
    ),
    responses={
        200: {"description": "Subscription recorded and activated"},
        400: {"description": "Invalid subscription ID or product mismatch"},
        401: {"description": "Not authenticated"},
        403: {"description": "Guest users cannot subscribe"},
        429: {"description": "Rate limit exceeded"},
    },
)
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


@router.post(
    "/webhook",
    summary="PayPal webhook receiver",
    description=(
        "Receives PayPal webhook events. Signature is verified using "
        "PayPal's certificate-based verification.\n\n"
        "**Handled events:**\n"
        "- `PAYMENT.CAPTURE.COMPLETED` — fulfills one-time purchases\n"
        "- `PAYMENT.CAPTURE.REFUNDED` — reverses wallet credits\n"
        "- `BILLING.SUBSCRIPTION.ACTIVATED` — activates subscription\n"
        "- `BILLING.SUBSCRIPTION.CANCELLED` — marks subscription cancelled\n"
        "- `BILLING.SUBSCRIPTION.EXPIRED` — deactivates subscription\n"
        "- `BILLING.SUBSCRIPTION.SUSPENDED` — suspends subscription\n\n"
        "**No user authentication** — PayPal signs the payload.\n\n"
        "Configure this URL in PayPal Developer Dashboard > "
        "Webhooks.\n\n"
        "**Rate limit:** 100 requests / 60 seconds per IP."
    ),
    responses={
        200: {"description": "Webhook processed successfully"},
        400: {"description": "Missing signature headers or invalid signature"},
    },
)
async def paypal_webhook(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _rl=Depends(_webhook_rate_limit),
):
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


@router.get(
    "/order-status",
    response_model=OrderStatusResponse,
    summary="Check PayPal order/checkout status",
    description=(
        "Poll the status of a PayPal checkout (order or subscription). "
        "Use this after the user returns from PayPal to confirm "
        "payment and fulfillment status.\n\n"
        "Pass either the `paypal_order_id` or `paypal_subscription_id` "
        "as the `checkout_id` parameter."
    ),
    responses={
        200: {
            "description": "Checkout status retrieved",
            "content": {
                "application/json": {
                    "example": {
                        "payment_status": "captured",
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
        404: {"description": "Checkout not found or does not belong to this user"},
    },
)
async def order_status(
    checkout_id: str = Query(
        ...,
        description=(
            "PayPal order ID or subscription ID to check. "
            "This is the ID returned from `POST /paypal/create-order` "
            "or `POST /paypal/subscription-approved`."
        ),
        example="5O190127TN364715T",
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    result = await get_order_status(db, checkout_id, user.account_id)
    if not result:
        raise HTTPException(status_code=404, detail="Checkout not found")
    return result
