"""
Stripe Checkout Service.

Handles checkout session creation, webhook processing, and failed event retry.
Credit units: gems (user.gems), NOT wallet cents (wallet_balance_minor).
"""

import logging
import math
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import stripe
from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.products import GemPackageConfig
from app.models.user import User
from app.models.wallet import StripeCheckout, StripeWebhookEvent
from app.services.gem_service import credit_gems, debit_gems
from app.services.product_pricing import get_product_info
from app.services.stripe_subscription_service import (
    activate_subscription_from_stripe,
    cancel_subscription_from_stripe,
)
from core.config import (
    STRIPE_CANCEL_URL,
    STRIPE_SECRET_KEY,
    STRIPE_SUCCESS_URL,
    STRIPE_WEBHOOK_SECRET,
)
from models import SubscriptionPlan

logger = logging.getLogger(__name__)

stripe.api_key = STRIPE_SECRET_KEY

# ---------------------------------------------------------------------------
# Customer management
# ---------------------------------------------------------------------------


async def get_or_create_stripe_customer(db: AsyncSession, user: User) -> str:
    """Return existing Stripe customer ID or create one."""
    if user.stripe_customer_id:
        return user.stripe_customer_id

    customer = stripe.Customer.create(
        email=user.email,
        name=user.username,
        metadata={"account_id": str(user.account_id)},
    )
    user.stripe_customer_id = customer.id
    await db.flush()
    logger.info(
        "Created Stripe customer %s for user %s", customer.id, user.account_id
    )
    return customer.id


# ---------------------------------------------------------------------------
# Checkout session creation
# ---------------------------------------------------------------------------


_SUPPORTED_STRIPE_PRODUCT_TYPES = {"gem_package", "consumable", "subscription"}


async def create_checkout_session(
    db: AsyncSession, user: User, product_id: str
) -> Dict[str, Any]:
    """Create a Stripe Checkout Session and local StripeCheckout record."""
    product_info = await get_product_info(db, product_id)

    if product_info["product_type"] not in _SUPPORTED_STRIPE_PRODUCT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product type '{product_info['product_type']}' is not available for web purchase",
        )

    customer_id = await get_or_create_stripe_customer(db, user)

    is_live = not (STRIPE_SECRET_KEY.startswith("sk_test_") or STRIPE_SECRET_KEY.startswith("rk_test_"))

    # Build Stripe line item based on product type
    if product_info["product_type"] == "subscription":
        stripe_price_id = product_info.get("stripe_price_id")
        if not stripe_price_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Subscription product '{product_id}' has no Stripe Price configured",
            )
        mode = "subscription"
        line_items = [{"price": stripe_price_id, "quantity": 1}]
    else:
        mode = "payment"
        line_items = [
            {
                "price_data": {
                    "currency": "usd",
                    "unit_amount": product_info["price_minor"],
                    "product_data": {
                        "name": product_info.get("product_name") or product_id,
                    },
                },
                "quantity": 1,
            }
        ]

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode=mode,
        line_items=line_items,
        success_url=STRIPE_SUCCESS_URL,
        cancel_url=STRIPE_CANCEL_URL,
        metadata={
            "user_id": str(user.account_id),
            "product_id": product_id,
            "product_type": product_info["product_type"],
        },
    )

    # Persist local record
    checkout = StripeCheckout(
        user_id=user.account_id,
        checkout_session_id=session.id,
        product_id=product_id,
        product_type=product_info["product_type"],
        price_minor=product_info["price_minor"],
        currency="usd",
        stripe_customer_id=customer_id,
        livemode=is_live,
    )
    db.add(checkout)
    await db.flush()

    return {"checkout_url": session.url, "session_id": session.id}


# ---------------------------------------------------------------------------
# Webhook event dedup
# ---------------------------------------------------------------------------


async def record_webhook_event(
    event: stripe.Event,
) -> bool:
    """Insert a StripeWebhookEvent row in its own transaction. Returns False if duplicate.

    Uses a dedicated session so the row survives rollback of the processing transaction.
    """
    from app.db import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        evt = StripeWebhookEvent(
            event_id=event.id,
            event_type=event.type,
            stripe_object_id=getattr(event.data.object, "id", None),
            livemode=event.livemode,
        )
        session.add(evt)
        try:
            await session.commit()
            return True
        except IntegrityError:
            await session.rollback()
            return False


# ---------------------------------------------------------------------------
# Webhook handlers
# ---------------------------------------------------------------------------


async def _get_or_reconstruct_checkout(
    db: AsyncSession, session_id: str, stripe_session: Any
) -> Optional[StripeCheckout]:
    """Look up StripeCheckout; reconstruct from metadata if missing."""
    stmt = select(StripeCheckout).where(
        StripeCheckout.checkout_session_id == session_id
    )
    result = await db.execute(stmt)
    checkout = result.scalar_one_or_none()

    if checkout:
        return checkout

    # Reconstruct from Stripe session metadata (atomicity recovery path)
    meta = stripe_session.get("metadata", {})
    user_id = meta.get("user_id")
    product_id = meta.get("product_id")
    product_type = meta.get("product_type")
    if not (user_id and product_id and product_type):
        logger.error(
            "Cannot reconstruct StripeCheckout for session %s: missing metadata",
            session_id,
        )
        return None

    logger.warning(
        "Reconstructing StripeCheckout for session %s from metadata", session_id
    )
    checkout = StripeCheckout(
        user_id=int(user_id),
        checkout_session_id=session_id,
        product_id=product_id,
        product_type=product_type,
        price_minor=stripe_session.get("amount_total", 0),
        currency=stripe_session.get("currency", "usd"),
        stripe_customer_id=stripe_session.get("customer"),
        livemode=stripe_session.get("livemode", False),
    )
    db.add(checkout)
    await db.flush()
    return checkout


async def handle_checkout_completed(
    db: AsyncSession, stripe_session: Dict[str, Any]
) -> None:
    """Handle checkout.session.completed — gem credit or subscription activation."""
    session_id = stripe_session["id"]
    checkout = await _get_or_reconstruct_checkout(db, session_id, stripe_session)
    if not checkout:
        return

    if checkout.fulfillment_status == "fulfilled":
        logger.info("Checkout %s already fulfilled, skipping", session_id)
        return

    checkout.payment_status = "paid"
    checkout.payment_intent_id = stripe_session.get("payment_intent")
    checkout.completed_at = datetime.utcnow()

    if checkout.product_type in ("gem_package", "consumable"):
        # Look up gems_amount from GemPackageConfig
        stmt = select(GemPackageConfig).where(
            GemPackageConfig.product_id == checkout.product_id
        )
        result = await db.execute(stmt)
        gem_config = result.scalar_one_or_none()
        if not gem_config:
            logger.error(
                "GemPackageConfig not found for product_id=%s", checkout.product_id
            )
            return

        await credit_gems(
            db,
            user_id=checkout.user_id,
            amount=gem_config.gems_amount,
            reason="stripe_purchase",
            ref_type="stripe_checkout",
            ref_id=session_id,
        )
        checkout.gems_credited = gem_config.gems_amount
        checkout.fulfillment_status = "fulfilled"
        logger.info(
            "Credited %d gems to user %s for checkout %s",
            gem_config.gems_amount,
            checkout.user_id,
            session_id,
        )

    elif checkout.product_type == "subscription":
        sub_id = stripe_session.get("subscription")
        checkout.stripe_subscription_id = sub_id

        # Look up plan
        stmt = select(SubscriptionPlan).where(
            (SubscriptionPlan.stripe_product_id == checkout.product_id)
            | (SubscriptionPlan.apple_product_id == checkout.product_id)
            | (SubscriptionPlan.google_product_id == checkout.product_id)
        )
        result = await db.execute(stmt)
        plan = result.scalar_one_or_none()
        if not plan:
            logger.error(
                "SubscriptionPlan not found for product_id=%s", checkout.product_id
            )
            return

        # Get period end from Stripe subscription
        stripe_sub = stripe.Subscription.retrieve(sub_id)
        period_end = datetime.utcfromtimestamp(stripe_sub.current_period_end)
        period_start = datetime.utcfromtimestamp(stripe_sub.current_period_start)

        await activate_subscription_from_stripe(
            db,
            user_id=checkout.user_id,
            plan=plan,
            stripe_subscription_id=sub_id,
            current_period_end=period_end,
            current_period_start=period_start,
            livemode=checkout.livemode,
        )
        checkout.fulfillment_status = "fulfilled"
        logger.info(
            "Activated subscription for user %s, plan %s, stripe_sub %s",
            checkout.user_id,
            plan.id,
            sub_id,
        )


async def handle_invoice_paid(
    db: AsyncSession, invoice: Dict[str, Any]
) -> None:
    """Handle invoice.payment_succeeded — subscription renewals."""
    if invoice.get("billing_reason") == "subscription_create":
        return  # Already handled by checkout.session.completed

    sub_id = invoice.get("subscription")
    if not sub_id:
        return

    # Look up StripeCheckout to update invoice ID
    stmt = select(StripeCheckout).where(
        StripeCheckout.stripe_subscription_id == sub_id
    )
    result = await db.execute(stmt)
    checkout = result.scalar_one_or_none()
    if checkout:
        checkout.stripe_invoice_id = invoice["id"]

    # Retrieve subscription for period info
    stripe_sub = stripe.Subscription.retrieve(sub_id)
    period_end = datetime.utcfromtimestamp(stripe_sub.current_period_end)
    period_start = datetime.utcfromtimestamp(stripe_sub.current_period_start)

    # Find the plan via checkout or by stripe_subscription_id on UserSubscription
    from models import UserSubscription

    us_stmt = select(UserSubscription).where(
        UserSubscription.stripe_subscription_id == sub_id
    )
    us_result = await db.execute(us_stmt)
    user_sub = us_result.scalar_one_or_none()
    if not user_sub:
        logger.warning("No UserSubscription found for stripe_sub %s", sub_id)
        return

    plan_stmt = select(SubscriptionPlan).where(
        SubscriptionPlan.id == user_sub.plan_id
    )
    plan_result = await db.execute(plan_stmt)
    plan = plan_result.scalar_one_or_none()
    if not plan:
        logger.error("SubscriptionPlan %s not found", user_sub.plan_id)
        return

    await activate_subscription_from_stripe(
        db,
        user_id=user_sub.user_id,
        plan=plan,
        stripe_subscription_id=sub_id,
        current_period_end=period_end,
        current_period_start=period_start,
        livemode=stripe_sub.livemode,
    )
    logger.info("Renewed subscription for stripe_sub %s", sub_id)


async def handle_invoice_failed(
    db: AsyncSession, invoice: Dict[str, Any]
) -> None:
    """Handle invoice.payment_failed — mark subscription past_due."""
    sub_id = invoice.get("subscription")
    if not sub_id:
        return

    from models import UserSubscription

    stmt = (
        select(UserSubscription)
        .where(UserSubscription.stripe_subscription_id == sub_id)
        .with_for_update()
    )
    result = await db.execute(stmt)
    user_sub = result.scalar_one_or_none()
    if user_sub:
        user_sub.status = "past_due"
        user_sub.updated_at = datetime.utcnow()
        logger.warning("Marked subscription %s as past_due", sub_id)


async def handle_subscription_deleted(
    db: AsyncSession, subscription: Dict[str, Any]
) -> None:
    """Handle customer.subscription.deleted."""
    sub_id = subscription["id"]
    await cancel_subscription_from_stripe(
        db, stripe_subscription_id=sub_id, new_status="canceled"
    )


async def handle_charge_refunded(
    db: AsyncSession, charge: Dict[str, Any]
) -> None:
    """Handle charge.refunded — cumulative gem reversal."""
    payment_intent_id = charge.get("payment_intent")
    if not payment_intent_id:
        return

    stmt = (
        select(StripeCheckout)
        .where(StripeCheckout.payment_intent_id == payment_intent_id)
        .with_for_update()
    )
    result = await db.execute(stmt)
    checkout = result.scalar_one_or_none()
    if not checkout:
        logger.warning(
            "No StripeCheckout found for payment_intent %s", payment_intent_id
        )
        return

    if not checkout.gems_credited or checkout.gems_credited <= 0:
        # Nothing to reverse (subscription or zero-gem purchase)
        return

    total_refunded = charge.get("amount_refunded", 0)
    if total_refunded <= 0:
        return

    # Cumulative reconciliation
    expected_total_reversed = math.floor(
        total_refunded / checkout.price_minor * checkout.gems_credited
    )
    this_reversal = expected_total_reversed - checkout.gems_reversed

    if this_reversal <= 0:
        logger.info(
            "No additional gems to reverse for checkout %s (already reversed %d)",
            checkout.checkout_session_id,
            checkout.gems_reversed,
        )
        return

    await debit_gems(
        db,
        user_id=checkout.user_id,
        amount=this_reversal,
        reason="stripe_refund",
        ref_type="stripe_refund",
        ref_id=checkout.checkout_session_id,
    )
    checkout.gems_reversed += this_reversal

    if total_refunded >= checkout.price_minor:
        checkout.fulfillment_status = "refunded"

    logger.info(
        "Refund processed: checkout=%s reversed=%d total_reversed=%d",
        checkout.checkout_session_id,
        this_reversal,
        checkout.gems_reversed,
    )


# ---------------------------------------------------------------------------
# Session status
# ---------------------------------------------------------------------------


async def get_session_status(
    db: AsyncSession, session_id: str, user_id: int
) -> Optional[Dict[str, Any]]:
    """Return checkout status for the frontend success page."""
    stmt = select(StripeCheckout).where(
        StripeCheckout.checkout_session_id == session_id,
        StripeCheckout.user_id == user_id,
    )
    result = await db.execute(stmt)
    checkout = result.scalar_one_or_none()
    if not checkout:
        return None

    return {
        "payment_status": checkout.payment_status,
        "fulfillment_status": checkout.fulfillment_status,
        "product_id": checkout.product_id,
        "price_minor": checkout.price_minor,
        "gems_credited": checkout.gems_credited,
        "completed_at": (
            checkout.completed_at.isoformat() if checkout.completed_at else None
        ),
    }


# ---------------------------------------------------------------------------
# Webhook retry job
# ---------------------------------------------------------------------------

_EVENT_HANDLERS = {
    "checkout.session.completed": handle_checkout_completed,
    "checkout.session.async_payment_succeeded": handle_checkout_completed,
    "invoice.payment_succeeded": handle_invoice_paid,
    "invoice.payment_failed": handle_invoice_failed,
    "customer.subscription.deleted": handle_subscription_deleted,
    "charge.refunded": handle_charge_refunded,
}


async def _process_event(db: AsyncSession, event: stripe.Event) -> None:
    """Dispatch a Stripe event to the correct handler."""
    handler = _EVENT_HANDLERS.get(event.type)
    if handler:
        await handler(db, event.data.object)


async def process_webhook_event(db: AsyncSession, event: stripe.Event) -> None:
    """
    Full webhook processing pipeline (Section 7a).

    1. Insert event as "received" in a separate transaction (dedup gate)
    2. Process business logic in the caller's session
    3. Mark processed / failed via separate session (survives rollback)
    """
    is_new = await record_webhook_event(event)
    if not is_new:
        logger.info("Duplicate webhook event %s, skipping", event.id)
        return

    try:
        await _process_event(db, event)
        await db.commit()
        # Mark processed in separate session (event row is in a different transaction)
        await _update_webhook_event_status(event.id, "processed")
    except Exception:
        await db.rollback()
        # Don't increment attempts here — retries do that, so max_attempts=3 means 3 retries
        await _update_webhook_event_status(event.id, "failed")
        logger.exception("Webhook processing failed for event %s", event.id)


async def _update_webhook_event_status(
    event_id: str, new_status: str, *, increment_attempts: bool = False
) -> None:
    """Update a StripeWebhookEvent's status in its own session."""
    from app.db import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            stmt = select(StripeWebhookEvent).where(
                StripeWebhookEvent.event_id == event_id
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
            "Failed to update webhook event %s to status %s", event_id, new_status
        )


async def retry_failed_stripe_events(db: AsyncSession) -> int:
    """
    Retry failed or stuck webhook events. Returns count of events retried.
    Called by APScheduler every 10 minutes.
    """
    now = datetime.utcnow()
    cutoff_stale = now - timedelta(minutes=5)
    cutoff_old = now - timedelta(hours=24)
    max_attempts = 3
    batch_size = 50

    stmt = (
        select(StripeWebhookEvent)
        .where(
            StripeWebhookEvent.attempts < max_attempts,
            StripeWebhookEvent.received_at > cutoff_old,
            or_(
                StripeWebhookEvent.status == "failed",
                # Stuck in "received" for > 5 min = likely crashed
                (
                    (StripeWebhookEvent.status == "received")
                    & (StripeWebhookEvent.received_at < cutoff_stale)
                ),
            ),
        )
        .limit(batch_size)
    )
    result = await db.execute(stmt)
    events = result.scalars().all()
    # Detach from session before processing (each retry uses its own session)
    event_ids = [(e.event_id, e.attempts) for e in events]

    retried = 0
    from app.db import AsyncSessionLocal

    for event_id, current_attempts in event_ids:
        try:
            stripe_event = stripe.Event.retrieve(
                event_id, api_key=STRIPE_SECRET_KEY
            )

            handler = _EVENT_HANDLERS.get(stripe_event.type)
            if handler:
                async with AsyncSessionLocal() as retry_session:
                    await handler(retry_session, stripe_event.data.object)
                    await retry_session.commit()

            await _update_webhook_event_status(
                event_id, "processed", increment_attempts=True
            )
            retried += 1
            logger.info("Retried event %s successfully", event_id)
        except Exception:
            await _update_webhook_event_status(
                event_id, "failed", increment_attempts=True
            )
            if current_attempts + 1 >= max_attempts:
                logger.error(
                    "Event %s exceeded max retries (%d), needs manual intervention",
                    event_id,
                    max_attempts,
                )
            logger.exception("Retry failed for event %s", event_id)

    return retried
