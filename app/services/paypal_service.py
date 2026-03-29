"""
PayPal Checkout Service.

Handles order creation, capture, subscription approval, webhook processing,
and failed event retry.

Credit units: gems (user.gems), NOT wallet cents (wallet_balance_minor).
"""

import json
import logging
import math
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from uuid import uuid4

from dateutil.parser import isoparse
from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.products import GemPackageConfig
from app.models.user import User
from app.models.wallet import PayPalCheckout, PayPalWebhookEvent
from app.services.asset_entitlement_service import grant_asset, revoke_asset
from app.services.gem_service import credit_gems, debit_gems
from app.services.paypal_client import PayPalClient
from app.services.paypal_subscription_service import (
    activate_subscription_from_paypal,
    cancel_subscription_from_paypal,
)
from app.services.product_pricing import get_product_info
from core.config import (
    PAYPAL_CLIENT_ID,
    PAYPAL_CLIENT_SECRET,
    PAYPAL_MODE,
    PAYPAL_WEBHOOK_ID,
)
from models import SubscriptionPlan, UserSubscription

logger = logging.getLogger(__name__)

# Module-level client instance
paypal_client = PayPalClient(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, PAYPAL_MODE)

_SUPPORTED_PAYPAL_PRODUCT_TYPES = {
    "gem_package",
    "consumable",
    "non_consumable",
    "subscription",
}

# ---------------------------------------------------------------------------
# Order creation (one-time purchases)
# ---------------------------------------------------------------------------


async def create_paypal_order(
    db: AsyncSession, user: User, product_id: str
) -> Dict[str, Any]:
    """Create a PayPal order for one-time purchases (gems, avatars, frames)."""
    product_info = await get_product_info(db, product_id)

    if product_info["product_type"] not in _SUPPORTED_PAYPAL_PRODUCT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product type '{product_info['product_type']}' not available for PayPal purchase",
        )

    if product_info["product_type"] == "subscription":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use /paypal/subscription-config for subscription products",
        )

    # Badges are not purchasable (admin-only)
    if product_id.startswith("BD"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Badges are not available for purchase",
        )

    # Block duplicate non-consumable purchases
    if product_info["product_type"] == "non_consumable":
        from app.services.asset_entitlement_service import check_already_owned

        if await check_already_owned(db, user_id=user.account_id, product_id=product_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You already own this item",
            )

    is_live = PAYPAL_MODE == "live"
    idempotency_key = str(uuid4())

    # custom_id encodes user+product for webhook recovery if local DB insert fails
    custom_id = f"{user.account_id}:{product_id}"

    order_resp = await paypal_client.create_order(
        amount_minor=product_info["price_minor"],
        currency="usd",
        reference_id=product_id,
        description=product_info.get("product_name") or product_id,
        request_id=idempotency_key,
        custom_id=custom_id,
    )

    checkout = PayPalCheckout(
        user_id=user.account_id,
        paypal_order_id=order_resp["id"],
        product_id=product_id,
        product_type=product_info["product_type"],
        price_minor=product_info["price_minor"],
        currency="usd",
        payment_status="created",
        idempotency_key=idempotency_key,
        livemode=is_live,
    )
    db.add(checkout)
    await db.flush()

    return {"paypal_order_id": order_resp["id"]}


# ---------------------------------------------------------------------------
# Subscription config (JS SDK creates subscription directly)
# ---------------------------------------------------------------------------


async def get_subscription_config(
    db: AsyncSession, user: User, product_id: str
) -> Dict[str, Any]:
    """Return the PayPal plan_id for the JS SDK to create a subscription."""
    product_info = await get_product_info(db, product_id)

    if product_info["product_type"] != "subscription":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only subscription products are supported by this endpoint",
        )

    paypal_plan_id = product_info.get("paypal_plan_id")
    if not paypal_plan_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Subscription '{product_id}' has no PayPal plan configured",
        )

    return {"paypal_plan_id": paypal_plan_id, "product_id": product_id}


# ---------------------------------------------------------------------------
# Subscription approval recording
# ---------------------------------------------------------------------------


async def record_subscription_approval(
    db: AsyncSession,
    user: User,
    product_id: str,
    paypal_subscription_id: str,
) -> Dict[str, Any]:
    """Record frontend onApprove so order-status polling works before webhook."""
    # Idempotent check
    stmt = select(PayPalCheckout).where(
        PayPalCheckout.paypal_subscription_id == paypal_subscription_id
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    if existing:
        return {
            "payment_status": existing.payment_status,
            "fulfillment_status": existing.fulfillment_status,
        }

    # Verify with PayPal API
    sub_resp = await paypal_client.get_subscription(paypal_subscription_id)
    product_info = await get_product_info(db, product_id)

    expected_plan_id = product_info.get("paypal_plan_id")
    actual_plan_id = sub_resp.get("plan_id")
    if expected_plan_id and actual_plan_id != expected_plan_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Subscription plan_id mismatch",
        )

    is_live = PAYPAL_MODE == "live"

    checkout = PayPalCheckout(
        user_id=user.account_id,
        paypal_subscription_id=paypal_subscription_id,
        product_id=product_id,
        product_type="subscription",
        price_minor=product_info["price_minor"],
        currency="usd",
        payment_status="approved",
        fulfillment_status="unfulfilled",
        livemode=is_live,
    )
    db.add(checkout)
    await db.flush()

    return {"payment_status": "approved", "fulfillment_status": "unfulfilled"}


# ---------------------------------------------------------------------------
# Order capture (primary fulfillment for one-time purchases)
# ---------------------------------------------------------------------------


async def _fulfill_checkout(
    db: AsyncSession, checkout: PayPalCheckout, ref_id: str
) -> None:
    """Fulfill a checkout based on product type. Shared by capture and webhook."""
    if checkout.product_type in ("gem_package", "consumable"):
        stmt = select(GemPackageConfig).where(
            GemPackageConfig.product_id == checkout.product_id
        )
        result = await db.execute(stmt)
        gem_config = result.scalar_one_or_none()
        if not gem_config:
            logger.error(
                "GemPackageConfig not found for product_id=%s", checkout.product_id
            )
            raise ValueError(f"GemPackageConfig not found: {checkout.product_id}")

        await credit_gems(
            db,
            user_id=checkout.user_id,
            amount=gem_config.gems_amount,
            reason="paypal_purchase",
            ref_type="paypal_checkout",
            ref_id=ref_id,
        )
        checkout.gems_credited = gem_config.gems_amount
        logger.info(
            "Credited %d gems to user %s for PayPal checkout %s",
            gem_config.gems_amount,
            checkout.user_id,
            ref_id,
        )

    elif checkout.product_type == "non_consumable":
        await grant_asset(
            db, user_id=checkout.user_id, product_id=checkout.product_id
        )
        checkout.asset_granted = True
        logger.info(
            "Granted asset %s to user %s for PayPal checkout %s",
            checkout.product_id,
            checkout.user_id,
            ref_id,
        )

    checkout.fulfillment_status = "fulfilled"


async def capture_paypal_order(
    db: AsyncSession, user: User, paypal_order_id: str
) -> Dict[str, Any]:
    """Capture a PayPal order and fulfill (gems or asset grant)."""
    stmt = select(PayPalCheckout).where(
        PayPalCheckout.paypal_order_id == paypal_order_id,
        PayPalCheckout.user_id == user.account_id,
    )
    result = await db.execute(stmt)
    checkout = result.scalar_one_or_none()

    if not checkout:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="PayPal checkout not found",
        )

    if checkout.fulfillment_status == "fulfilled":
        return {
            "payment_status": checkout.payment_status,
            "fulfillment_status": checkout.fulfillment_status,
            "gems_credited": checkout.gems_credited,
            "asset_granted": checkout.asset_granted,
        }

    capture_request_id = f"{checkout.idempotency_key}_capture"
    capture_resp = await paypal_client.capture_order(
        paypal_order_id, request_id=capture_request_id
    )

    capture_status = capture_resp.get("status")
    if capture_status != "COMPLETED":
        if capture_status == "PENDING":
            checkout.payment_status = "pending"
            await db.flush()
            return {
                "payment_status": "pending",
                "fulfillment_status": "unfulfilled",
                "gems_credited": None,
                "asset_granted": False,
            }
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"PayPal capture returned status: {capture_status}",
        )

    # Extract capture details and verify amount
    captures = (
        capture_resp.get("purchase_units", [{}])[0]
        .get("payments", {})
        .get("captures", [])
    )
    if captures:
        checkout.paypal_capture_id = captures[0].get("id")
        # Verify captured amount matches expected price
        captured_amount = captures[0].get("amount", {})
        captured_value = captured_amount.get("value", "0")
        captured_minor = int(float(captured_value) * 100)
        if captured_minor != checkout.price_minor:
            logger.error(
                "Capture amount mismatch: expected=%d got=%d for order %s",
                checkout.price_minor,
                captured_minor,
                paypal_order_id,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Captured amount does not match expected price",
            )

    payer = capture_resp.get("payer", {})
    checkout.paypal_payer_id = payer.get("payer_id")

    # Update payer ID on user if not set
    if checkout.paypal_payer_id:
        user_stmt = select(User).where(User.account_id == user.account_id)
        user_result = await db.execute(user_stmt)
        db_user = user_result.scalar_one_or_none()
        if db_user and not db_user.paypal_payer_id:
            db_user.paypal_payer_id = checkout.paypal_payer_id

    checkout.payment_status = "captured"
    checkout.captured_at = datetime.utcnow()

    await _fulfill_checkout(db, checkout, paypal_order_id)

    return {
        "payment_status": checkout.payment_status,
        "fulfillment_status": checkout.fulfillment_status,
        "gems_credited": checkout.gems_credited,
        "asset_granted": checkout.asset_granted,
    }


# ---------------------------------------------------------------------------
# Webhook event dedup
# ---------------------------------------------------------------------------


async def record_paypal_webhook_event(
    event_id: str,
    event_type: str,
    resource_id: Optional[str],
    raw_body: bytes,
    livemode: bool = False,
) -> bool:
    """Insert PayPalWebhookEvent in its own transaction. Returns False if duplicate."""
    from app.db import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        evt = PayPalWebhookEvent(
            event_id=event_id,
            event_type=event_type,
            resource_id=resource_id,
            raw_payload=raw_body.decode("utf-8", errors="replace"),
            livemode=livemode,
        )
        session.add(evt)
        try:
            await session.commit()
            return True
        except IntegrityError:
            await session.rollback()
            return False


# ---------------------------------------------------------------------------
# Checkout reconstruction (recovery when local DB insert fails)
# ---------------------------------------------------------------------------


async def _reconstruct_checkout_from_order(
    db: AsyncSession, order_id: str
) -> Optional[PayPalCheckout]:
    """Reconstruct a PayPalCheckout from PayPal order metadata (custom_id).

    Called when webhook fires but checkout row is missing (DB insert failed
    after PayPal order creation succeeded).
    """
    try:
        order_data = await paypal_client.get_order(order_id)
    except Exception:
        logger.exception("Failed to fetch PayPal order %s for reconstruction", order_id)
        return None

    purchase_units = order_data.get("purchase_units", [])
    if not purchase_units:
        return None

    custom_id = purchase_units[0].get("custom_id", "")
    if ":" not in custom_id:
        logger.error("Cannot reconstruct checkout for order %s: invalid custom_id '%s'", order_id, custom_id)
        return None

    user_id_str, product_id = custom_id.split(":", 1)
    try:
        user_id = int(user_id_str)
    except (ValueError, TypeError):
        logger.error("Cannot reconstruct checkout for order %s: invalid user_id in custom_id", order_id)
        return None

    # Verify user exists
    user_result = await db.execute(select(User).where(User.account_id == user_id))
    if not user_result.scalar_one_or_none():
        logger.error("Cannot reconstruct checkout for order %s: user %s not found", order_id, user_id)
        return None

    product_info = await get_product_info(db, product_id)

    is_live = PAYPAL_MODE == "live"
    checkout = PayPalCheckout(
        user_id=user_id,
        paypal_order_id=order_id,
        product_id=product_id,
        product_type=product_info["product_type"],
        price_minor=product_info["price_minor"],
        currency="usd",
        payment_status="approved",
        fulfillment_status="unfulfilled",
        livemode=is_live,
    )
    db.add(checkout)
    await db.flush()
    logger.warning("Reconstructed PayPalCheckout for order %s from custom_id metadata", order_id)
    return checkout


# ---------------------------------------------------------------------------
# Webhook handlers — Orders
# ---------------------------------------------------------------------------


async def handle_order_approved(
    db: AsyncSession, resource: Dict[str, Any]
) -> None:
    """Recovery for uncaptured orders (browser failure)."""
    order_id = resource.get("id")
    if not order_id:
        return

    stmt = select(PayPalCheckout).where(
        PayPalCheckout.paypal_order_id == order_id
    )
    result = await db.execute(stmt)
    checkout = result.scalar_one_or_none()

    if not checkout:
        # Reconstruct from PayPal order metadata (custom_id = "user_id:product_id")
        checkout = await _reconstruct_checkout_from_order(db, order_id)
        if not checkout:
            logger.warning("handle_order_approved: no checkout for order %s", order_id)
            return

    if checkout.fulfillment_status == "fulfilled":
        return

    capture_request_id = f"{checkout.idempotency_key}_capture" if checkout.idempotency_key else str(uuid4())
    capture_resp = await paypal_client.capture_order(
        order_id, request_id=capture_request_id
    )

    if capture_resp.get("status") != "COMPLETED":
        checkout.payment_status = capture_resp.get("status", "").lower()
        logger.warning(
            "Webhook order capture returned non-COMPLETED: %s for order %s",
            capture_resp.get("status"),
            order_id,
        )
        return

    captures = (
        capture_resp.get("purchase_units", [{}])[0]
        .get("payments", {})
        .get("captures", [])
    )
    if captures:
        checkout.paypal_capture_id = captures[0].get("id")

    payer = capture_resp.get("payer", {})
    checkout.paypal_payer_id = payer.get("payer_id")
    checkout.payment_status = "captured"
    checkout.captured_at = datetime.utcnow()

    await _fulfill_checkout(db, checkout, order_id)
    logger.warning(
        "Order %s captured via webhook recovery — browser did not complete capture",
        order_id,
    )


async def handle_capture_completed(
    db: AsyncSession, resource: Dict[str, Any]
) -> None:
    """Secondary recovery (capture succeeded but local DB commit failed)."""
    # Extract order_id from supplementary_data
    order_id = (
        resource.get("supplementary_data", {})
        .get("related_ids", {})
        .get("order_id")
    )
    if not order_id:
        # Try to find by capture ID
        capture_id = resource.get("id")
        if capture_id:
            stmt = select(PayPalCheckout).where(
                PayPalCheckout.paypal_capture_id == capture_id
            )
            result = await db.execute(stmt)
            checkout = result.scalar_one_or_none()
            if checkout and checkout.fulfillment_status == "fulfilled":
                return
            if checkout:
                await _fulfill_checkout(db, checkout, checkout.paypal_order_id or capture_id)
                checkout.payment_status = "captured"
                checkout.captured_at = datetime.utcnow()
                return
        return

    stmt = select(PayPalCheckout).where(
        PayPalCheckout.paypal_order_id == order_id
    )
    result = await db.execute(stmt)
    checkout = result.scalar_one_or_none()

    if not checkout:
        # Reconstruct from PayPal order metadata
        checkout = await _reconstruct_checkout_from_order(db, order_id)
        if not checkout:
            return

    if checkout.fulfillment_status == "fulfilled":
        return

    checkout.payment_status = "captured"
    checkout.captured_at = datetime.utcnow()
    if not checkout.paypal_capture_id:
        checkout.paypal_capture_id = resource.get("id")

    await _fulfill_checkout(db, checkout, order_id)


async def handle_capture_pending(
    db: AsyncSession, resource: Dict[str, Any]
) -> None:
    """Capture is pending (eCheck, review hold) — do NOT fulfill."""
    order_id = (
        resource.get("supplementary_data", {})
        .get("related_ids", {})
        .get("order_id")
    )
    if not order_id:
        return

    stmt = select(PayPalCheckout).where(
        PayPalCheckout.paypal_order_id == order_id
    )
    result = await db.execute(stmt)
    checkout = result.scalar_one_or_none()
    if checkout:
        checkout.payment_status = "pending"
        logger.info("PayPal capture pending for order %s", order_id)


async def handle_capture_denied(
    db: AsyncSession, resource: Dict[str, Any]
) -> None:
    """Capture denied (fraud, insufficient funds)."""
    order_id = (
        resource.get("supplementary_data", {})
        .get("related_ids", {})
        .get("order_id")
    )
    if not order_id:
        return

    stmt = select(PayPalCheckout).where(
        PayPalCheckout.paypal_order_id == order_id
    )
    result = await db.execute(stmt)
    checkout = result.scalar_one_or_none()
    if checkout:
        checkout.payment_status = "denied"
        logger.warning("PayPal capture denied for order %s", order_id)


async def handle_payment_approval_reversed(
    db: AsyncSession, resource: Dict[str, Any]
) -> None:
    """Payment approval reversed for a previously approved order."""
    order_id = resource.get("id")
    if not order_id:
        return

    stmt = select(PayPalCheckout).where(
        PayPalCheckout.paypal_order_id == order_id
    )
    result = await db.execute(stmt)
    checkout = result.scalar_one_or_none()
    if not checkout:
        return

    if checkout.fulfillment_status != "fulfilled":
        checkout.payment_status = "failed"
    else:
        logger.error(
            "Payment approval reversed for already-fulfilled order %s", order_id
        )


# ---------------------------------------------------------------------------
# Webhook handlers — Refunds (one-time purchases)
# ---------------------------------------------------------------------------


async def handle_payment_refunded(
    db: AsyncSession, resource: Dict[str, Any]
) -> None:
    """Fulfillment reversal for one-time purchases based on product type."""
    # The resource is a capture object; extract the order_id
    order_id = (
        resource.get("supplementary_data", {})
        .get("related_ids", {})
        .get("order_id")
    )
    capture_id = resource.get("id")

    checkout = None
    if order_id:
        stmt = select(PayPalCheckout).where(
            PayPalCheckout.paypal_order_id == order_id
        ).with_for_update()
        result = await db.execute(stmt)
        checkout = result.scalar_one_or_none()

    if not checkout and capture_id:
        stmt = select(PayPalCheckout).where(
            PayPalCheckout.paypal_capture_id == capture_id
        ).with_for_update()
        result = await db.execute(stmt)
        checkout = result.scalar_one_or_none()

    if not checkout:
        logger.warning(
            "handle_payment_refunded: no checkout found for order=%s capture=%s",
            order_id,
            capture_id,
        )
        return

    # Calculate total refunded from the refund breakdown
    seller_breakdown = (
        resource.get("seller_payable_breakdown", {})
    )
    total_refund_value = seller_breakdown.get("total_refunded_amount", {}).get("value")
    if total_refund_value is not None:
        total_refunded = int(float(total_refund_value) * 100)
    else:
        # Fallback: use the refund amount from the resource
        amount = resource.get("amount", {})
        total_refunded = int(float(amount.get("value", "0")) * 100)

    if total_refunded <= 0:
        return

    if checkout.product_type in ("gem_package", "consumable"):
        # Cumulative gem reconciliation (same as Stripe)
        if checkout.gems_credited and checkout.gems_credited > 0:
            expected_total_reversed = math.floor(
                total_refunded / checkout.price_minor * checkout.gems_credited
            )
            this_reversal = expected_total_reversed - checkout.gems_reversed

            if this_reversal > 0:
                await debit_gems(
                    db,
                    user_id=checkout.user_id,
                    amount=this_reversal,
                    reason="paypal_refund",
                    ref_type="paypal_refund",
                    ref_id=checkout.paypal_order_id or str(capture_id),
                )
                checkout.gems_reversed += this_reversal

    elif checkout.product_type == "non_consumable":
        # All-or-nothing: revoke only on full refund
        if total_refunded >= checkout.price_minor and checkout.asset_granted:
            await revoke_asset(
                db, user_id=checkout.user_id, product_id=checkout.product_id
            )
            checkout.asset_granted = False

    if total_refunded >= checkout.price_minor:
        checkout.fulfillment_status = "refunded"

    logger.info(
        "Refund processed: checkout=%s total_refunded=%d product_type=%s",
        checkout.paypal_order_id,
        total_refunded,
        checkout.product_type,
    )


# ---------------------------------------------------------------------------
# Webhook handlers — Subscriptions
# ---------------------------------------------------------------------------


async def handle_subscription_activated(
    db: AsyncSession, resource: Dict[str, Any]
) -> None:
    """Primary fulfillment for subscriptions (no capture step)."""
    subscription_id = resource.get("id")
    if not subscription_id:
        return

    # Look up PayPalCheckout
    stmt = select(PayPalCheckout).where(
        PayPalCheckout.paypal_subscription_id == subscription_id
    )
    result = await db.execute(stmt)
    checkout = result.scalar_one_or_none()

    # Resolve plan from resource.plan_id
    resource_plan_id = resource.get("plan_id")
    plan_stmt = select(SubscriptionPlan).where(
        SubscriptionPlan.paypal_plan_id == resource_plan_id
    )
    plan_result = await db.execute(plan_stmt)
    plan = plan_result.scalar_one_or_none()
    if not plan:
        logger.error(
            "SubscriptionPlan not found for paypal_plan_id=%s", resource_plan_id
        )
        raise ValueError(f"SubscriptionPlan not found: {resource_plan_id}")

    if not checkout:
        # Create from webhook data — custom_id is mandatory
        custom_id = resource.get("custom_id")
        if not custom_id:
            logger.error(
                "No custom_id in subscription activation for %s", subscription_id
            )
            raise ValueError("custom_id is required for subscription activation")

        try:
            user_id = int(custom_id)
        except (ValueError, TypeError):
            logger.error("Invalid custom_id '%s' for subscription %s", custom_id, subscription_id)
            raise ValueError(f"Invalid custom_id: {custom_id}")

        # Verify user exists
        user_result = await db.execute(
            select(User).where(User.account_id == user_id)
        )
        if not user_result.scalar_one_or_none():
            logger.error("User %s not found for subscription %s", user_id, subscription_id)
            raise ValueError(f"User not found: {user_id}")

        is_live = PAYPAL_MODE == "live"
        checkout = PayPalCheckout(
            user_id=user_id,
            paypal_subscription_id=subscription_id,
            product_id=plan.paypal_product_id or plan.apple_product_id or plan.google_product_id or plan.stripe_product_id or str(plan.id),
            product_type="subscription",
            price_minor=plan.unit_amount_minor or 0,
            currency="usd",
            payment_status="approved",
            fulfillment_status="unfulfilled",
            livemode=is_live,
        )
        db.add(checkout)
        await db.flush()
    else:
        # Plan mismatch check
        product_info = await get_product_info(db, checkout.product_id)
        expected_plan_id = product_info.get("paypal_plan_id")
        if expected_plan_id and resource_plan_id != expected_plan_id:
            logger.error(
                "Plan mismatch: checkout product_id=%s expects paypal_plan_id=%s but webhook has %s",
                checkout.product_id,
                expected_plan_id,
                resource_plan_id,
            )
            raise ValueError("Plan mismatch between checkout record and webhook")

    if checkout.fulfillment_status == "fulfilled":
        return

    # Get billing period
    billing_info = resource.get("billing_info", {})
    next_billing_time = billing_info.get("next_billing_time")
    if next_billing_time:
        current_period_end = isoparse(next_billing_time).replace(tzinfo=None)
    else:
        # Fallback: 30 days from now
        current_period_end = datetime.utcnow() + timedelta(days=30)

    await activate_subscription_from_paypal(
        db,
        user_id=checkout.user_id,
        plan=plan,
        paypal_subscription_id=subscription_id,
        current_period_end=current_period_end,
        livemode=checkout.livemode,
    )

    checkout.fulfillment_status = "fulfilled"
    checkout.payment_status = "active"
    logger.info(
        "Activated PayPal subscription %s for user %s, plan %s",
        subscription_id,
        checkout.user_id,
        plan.id,
    )


async def handle_subscription_cancelled(
    db: AsyncSession, resource: Dict[str, Any]
) -> None:
    """Handle BILLING.SUBSCRIPTION.CANCELLED."""
    subscription_id = resource.get("id")
    if subscription_id:
        await cancel_subscription_from_paypal(
            db, paypal_subscription_id=subscription_id
        )


async def handle_sale_completed(
    db: AsyncSession, resource: Dict[str, Any]
) -> None:
    """Subscription renewal payment."""
    subscription_id = resource.get("billing_agreement_id")
    if not subscription_id:
        return

    stmt = select(UserSubscription).where(
        UserSubscription.paypal_subscription_id == subscription_id
    )
    result = await db.execute(stmt)
    user_sub = result.scalar_one_or_none()
    if not user_sub:
        logger.warning(
            "handle_sale_completed: no UserSubscription for paypal_sub %s",
            subscription_id,
        )
        return

    plan_stmt = select(SubscriptionPlan).where(
        SubscriptionPlan.id == user_sub.plan_id
    )
    plan_result = await db.execute(plan_stmt)
    plan = plan_result.scalar_one_or_none()
    if not plan:
        logger.error("SubscriptionPlan %s not found", user_sub.plan_id)
        return

    # Fetch next billing time from PayPal
    try:
        sub_resp = await paypal_client.get_subscription(subscription_id)
        next_billing = sub_resp.get("billing_info", {}).get("next_billing_time")
        if next_billing:
            period_end = isoparse(next_billing).replace(tzinfo=None)
        else:
            period_end = datetime.utcnow() + timedelta(days=30)
    except Exception:
        logger.exception(
            "Failed to fetch subscription %s from PayPal, using 30-day default",
            subscription_id,
        )
        period_end = datetime.utcnow() + timedelta(days=30)

    await activate_subscription_from_paypal(
        db,
        user_id=user_sub.user_id,
        plan=plan,
        paypal_subscription_id=subscription_id,
        current_period_end=period_end,
        livemode=user_sub.livemode,
    )
    logger.info("Renewed PayPal subscription %s", subscription_id)


async def handle_sale_refunded(
    db: AsyncSession, resource: Dict[str, Any]
) -> None:
    """Subscription payment refunded — leave entitlement active, mark past_due."""
    subscription_id = resource.get("billing_agreement_id")
    if not subscription_id:
        return

    stmt = (
        select(UserSubscription)
        .where(UserSubscription.paypal_subscription_id == subscription_id)
        .with_for_update()
    )
    result = await db.execute(stmt)
    user_sub = result.scalar_one_or_none()
    if user_sub:
        user_sub.status = "past_due"
        user_sub.updated_at = datetime.utcnow()
        logger.warning(
            "PayPal subscription %s payment refunded — marked past_due", subscription_id
        )


async def handle_sale_reversed(
    db: AsyncSession, resource: Dict[str, Any]
) -> None:
    """Subscription payment reversed (chargeback) — suspend immediately."""
    subscription_id = resource.get("billing_agreement_id")
    if not subscription_id:
        return

    stmt = (
        select(UserSubscription)
        .where(UserSubscription.paypal_subscription_id == subscription_id)
        .with_for_update()
    )
    result = await db.execute(stmt)
    user_sub = result.scalar_one_or_none()
    if user_sub:
        user_sub.status = "suspended"
        user_sub.updated_at = datetime.utcnow()
        logger.error(
            "PayPal subscription %s payment reversed — suspended, manual review required",
            subscription_id,
        )


# ---------------------------------------------------------------------------
# Webhook processing pipeline
# ---------------------------------------------------------------------------


_EVENT_HANDLERS = {
    "CHECKOUT.ORDER.APPROVED": handle_order_approved,
    "CHECKOUT.PAYMENT-APPROVAL.REVERSED": handle_payment_approval_reversed,
    "PAYMENT.CAPTURE.COMPLETED": handle_capture_completed,
    "PAYMENT.CAPTURE.PENDING": handle_capture_pending,
    "PAYMENT.CAPTURE.DENIED": handle_capture_denied,
    "PAYMENT.CAPTURE.REFUNDED": handle_payment_refunded,
    "BILLING.SUBSCRIPTION.ACTIVATED": handle_subscription_activated,
    "BILLING.SUBSCRIPTION.CANCELLED": handle_subscription_cancelled,
    "PAYMENT.SALE.COMPLETED": handle_sale_completed,
    "PAYMENT.SALE.REFUNDED": handle_sale_refunded,
    "PAYMENT.SALE.REVERSED": handle_sale_reversed,
}


async def _update_paypal_webhook_event_status(
    event_id: str, new_status: str, *, increment_attempts: bool = False
) -> None:
    """Update a PayPalWebhookEvent's status in its own session."""
    from app.db import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            stmt = select(PayPalWebhookEvent).where(
                PayPalWebhookEvent.event_id == event_id
            )
            result = await session.execute(stmt)
            evt_row = result.scalar_one_or_none()
            if evt_row:
                evt_row.status = new_status
                if increment_attempts:
                    evt_row.attempts += 1
                if new_status == "processed":
                    evt_row.processed_at = datetime.utcnow()
                await session.commit()
    except Exception:
        logger.exception(
            "Failed to update PayPal webhook event %s to status %s",
            event_id,
            new_status,
        )


async def process_paypal_webhook(
    db: AsyncSession, event_body: Dict[str, Any], raw_body: bytes
) -> None:
    """Full webhook processing pipeline."""
    event_id = event_body.get("id", "")
    event_type = event_body.get("event_type", "")
    resource = event_body.get("resource", {})
    resource_id = resource.get("id")

    is_new = await record_paypal_webhook_event(
        event_id=event_id,
        event_type=event_type,
        resource_id=resource_id,
        raw_body=raw_body,
        livemode=event_body.get("resource", {}).get("livemode", False),
    )
    if not is_new:
        logger.info("Duplicate PayPal webhook event %s, skipping", event_id)
        return

    handler = _EVENT_HANDLERS.get(event_type)
    if not handler:
        logger.info("Unhandled PayPal event type: %s", event_type)
        await _update_paypal_webhook_event_status(event_id, "processed")
        return

    try:
        await handler(db, resource)
        await db.commit()
        await _update_paypal_webhook_event_status(event_id, "processed")
    except Exception:
        await db.rollback()
        await _update_paypal_webhook_event_status(event_id, "failed")
        logger.exception("PayPal webhook processing failed for event %s", event_id)


# ---------------------------------------------------------------------------
# Order status
# ---------------------------------------------------------------------------


async def get_order_status(
    db: AsyncSession, checkout_id: str, user_id: int
) -> Optional[Dict[str, Any]]:
    """Return checkout status for frontend polling."""
    stmt = select(PayPalCheckout).where(
        PayPalCheckout.user_id == user_id,
        or_(
            PayPalCheckout.paypal_order_id == checkout_id,
            PayPalCheckout.paypal_subscription_id == checkout_id,
        ),
    )
    result = await db.execute(stmt)
    checkout = result.scalar_one_or_none()
    if not checkout:
        return None

    return {
        "payment_status": checkout.payment_status,
        "fulfillment_status": checkout.fulfillment_status,
        "product_id": checkout.product_id,
        "product_type": checkout.product_type,
        "gems_credited": checkout.gems_credited,
        "asset_granted": checkout.asset_granted,
    }


# ---------------------------------------------------------------------------
# Retry job
# ---------------------------------------------------------------------------


async def retry_failed_paypal_events(db: AsyncSession) -> int:
    """
    Retry failed or stuck PayPal webhook events.
    Replays from raw_payload first; falls back to PayPal event API.
    Called by APScheduler every 10 minutes.
    """
    now = datetime.utcnow()
    cutoff_stale = now - timedelta(minutes=5)
    cutoff_old = now - timedelta(hours=24)
    max_attempts = 3
    batch_size = 50

    stmt = (
        select(PayPalWebhookEvent)
        .where(
            PayPalWebhookEvent.attempts < max_attempts,
            PayPalWebhookEvent.received_at > cutoff_old,
            or_(
                PayPalWebhookEvent.status == "failed",
                (
                    (PayPalWebhookEvent.status == "received")
                    & (PayPalWebhookEvent.received_at < cutoff_stale)
                ),
            ),
        )
        .limit(batch_size)
    )
    result = await db.execute(stmt)
    events = result.scalars().all()
    event_data = [
        (e.event_id, e.raw_payload, e.attempts) for e in events
    ]

    retried = 0
    from app.db import AsyncSessionLocal

    for event_id, raw_payload, current_attempts in event_data:
        try:
            # Precedence: raw_payload first, then PayPal API fallback
            event_body = None
            if raw_payload:
                try:
                    event_body = json.loads(raw_payload)
                except (json.JSONDecodeError, TypeError):
                    pass

            if not event_body:
                fetched = await paypal_client.get_webhook_event(event_id)
                event_body = fetched

            event_type = event_body.get("event_type", "")
            resource = event_body.get("resource", {})
            handler = _EVENT_HANDLERS.get(event_type)

            if handler:
                async with AsyncSessionLocal() as retry_session:
                    await handler(retry_session, resource)
                    await retry_session.commit()

            await _update_paypal_webhook_event_status(
                event_id, "processed", increment_attempts=True
            )
            retried += 1
            logger.info("Retried PayPal event %s successfully", event_id)
        except Exception:
            await _update_paypal_webhook_event_status(
                event_id, "failed", increment_attempts=True
            )
            if current_attempts + 1 >= max_attempts:
                logger.error(
                    "PayPal event %s exceeded max retries (%d), needs manual intervention",
                    event_id,
                    max_attempts,
                )
            logger.exception("Retry failed for PayPal event %s", event_id)

    return retried
