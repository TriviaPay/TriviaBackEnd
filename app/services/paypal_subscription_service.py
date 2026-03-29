"""
PayPal Subscription Adapter.

Same pattern as stripe_subscription_service.py: thin adapter that operates
directly on UserSubscription without IAP-specific assumptions.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import SubscriptionPlan, UserSubscription

logger = logging.getLogger(__name__)


async def activate_subscription_from_paypal(
    db: AsyncSession,
    *,
    user_id: int,
    plan: SubscriptionPlan,
    paypal_subscription_id: str,
    current_period_end: datetime,
    current_period_start: Optional[datetime] = None,
    livemode: bool = True,
) -> Dict[str, Any]:
    """Create or renew a UserSubscription from PayPal webhook data."""
    stmt = (
        select(UserSubscription)
        .where(
            UserSubscription.user_id == user_id,
            UserSubscription.plan_id == plan.id,
        )
        .with_for_update()
    )
    result = await db.execute(stmt)
    subscription = result.scalar_one_or_none()

    now = datetime.utcnow()

    if subscription:
        subscription.status = "active"
        subscription.paypal_subscription_id = paypal_subscription_id
        subscription.current_period_start = current_period_start or now
        subscription.current_period_end = current_period_end
        subscription.cancel_at_period_end = False
        subscription.livemode = livemode
        subscription.updated_at = now
        logger.info(
            "Renewed PayPal subscription: user=%s plan=%s sub_id=%s period_end=%s",
            user_id,
            plan.id,
            paypal_subscription_id,
            current_period_end,
        )
    else:
        subscription = UserSubscription(
            user_id=user_id,
            plan_id=plan.id,
            status="active",
            paypal_subscription_id=paypal_subscription_id,
            current_period_start=current_period_start or now,
            current_period_end=current_period_end,
            cancel_at_period_end=False,
            livemode=livemode,
            created_at=now,
            updated_at=now,
        )
        db.add(subscription)
        logger.info(
            "Created PayPal subscription: user=%s plan=%s sub_id=%s period_end=%s",
            user_id,
            plan.id,
            paypal_subscription_id,
            current_period_end,
        )

    await db.flush()

    return {
        "subscription_id": subscription.id,
        "plan_name": plan.name,
        "status": subscription.status,
        "current_period_start": (
            subscription.current_period_start.isoformat()
            if subscription.current_period_start
            else None
        ),
        "current_period_end": (
            subscription.current_period_end.isoformat()
            if subscription.current_period_end
            else None
        ),
    }


async def cancel_subscription_from_paypal(
    db: AsyncSession,
    *,
    paypal_subscription_id: str,
    new_status: str = "canceled",
) -> bool:
    """Cancel/expire a UserSubscription by paypal_subscription_id."""
    stmt = (
        select(UserSubscription)
        .where(UserSubscription.paypal_subscription_id == paypal_subscription_id)
        .with_for_update()
    )
    result = await db.execute(stmt)
    subscription = result.scalar_one_or_none()

    if not subscription:
        logger.warning(
            "cancel_subscription_from_paypal: no subscription found for %s",
            paypal_subscription_id,
        )
        return False

    now = datetime.utcnow()
    subscription.status = new_status
    subscription.updated_at = now
    if new_status in ("canceled", "revoked"):
        subscription.canceled_at = now
    if new_status == "expired":
        subscription.cancel_at_period_end = False

    logger.info(
        "Canceled PayPal subscription: sub_id=%s new_status=%s user=%s",
        paypal_subscription_id,
        new_status,
        subscription.user_id,
    )
    return True
