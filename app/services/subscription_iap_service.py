"""Subscription activation from IAP purchases.

When a user purchases a subscription product through Apple or Google IAP,
this service creates or renews the corresponding UserSubscription record.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models import SubscriptionPlan, UserSubscription

logger = logging.getLogger(__name__)


async def lookup_subscription_plan(
    db: AsyncSession, *, platform: str, product_id: str
) -> Optional[SubscriptionPlan]:
    """Look up a SubscriptionPlan by its platform-specific product ID.

    Returns None if the product_id doesn't map to a subscription plan.
    """
    if platform == "apple":
        stmt = select(SubscriptionPlan).where(
            SubscriptionPlan.apple_product_id == product_id
        )
    elif platform == "google":
        stmt = select(SubscriptionPlan).where(
            SubscriptionPlan.google_product_id == product_id
        )
    else:
        return None

    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def _compute_period_end(start: datetime, plan: SubscriptionPlan) -> datetime:
    """Compute the subscription period end from start + plan interval."""
    interval = plan.interval or plan.billing_interval or "month"
    count = plan.interval_count or 1

    if interval == "day":
        return start + relativedelta(days=count)
    elif interval == "week":
        return start + relativedelta(weeks=count)
    elif interval == "year":
        return start + relativedelta(years=count)
    else:  # default to month
        return start + relativedelta(months=count)


async def activate_subscription_from_iap(
    db: AsyncSession,
    *,
    user_id: int,
    plan: SubscriptionPlan,
    receipt_id: int,
    livemode: bool = True,
) -> Dict[str, Any]:
    """Create or renew a UserSubscription after a verified IAP purchase.

    - If the user has an active subscription for this plan, extend the period.
    - If the user has an expired/canceled subscription, reactivate it.
    - If no subscription exists, create a new one.

    Returns a dict with subscription details for the API response.
    """
    now = datetime.now(timezone.utc)

    # Find existing subscription for this user + plan
    stmt = (
        select(UserSubscription)
        .where(
            and_(
                UserSubscription.user_id == user_id,
                UserSubscription.plan_id == plan.id,
            )
        )
        .with_for_update()
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        if existing.status == "active" and existing.current_period_end and existing.current_period_end > now:
            # Extend from current period end
            new_end = _compute_period_end(existing.current_period_end, plan)
            existing.current_period_end = new_end
        else:
            # Reactivate: start fresh from now
            existing.status = "active"
            existing.current_period_start = now
            existing.current_period_end = _compute_period_end(now, plan)
            existing.cancel_at_period_end = False
            existing.cancel_at = None
            existing.canceled_at = None

        existing.livemode = livemode
        existing.updated_at = now
        subscription = existing
        logger.info(
            "Subscription renewed: user=%s plan=%s period_end=%s",
            user_id, plan.id, subscription.current_period_end,
        )
    else:
        period_end = _compute_period_end(now, plan)
        subscription = UserSubscription(
            user_id=user_id,
            plan_id=plan.id,
            status="active",
            current_period_start=now,
            current_period_end=period_end,
            cancel_at_period_end=False,
            livemode=livemode,
            created_at=now,
            updated_at=now,
        )
        db.add(subscription)
        logger.info(
            "Subscription created: user=%s plan=%s period_end=%s",
            user_id, plan.id, period_end,
        )

    try:
        await db.flush()
    except IntegrityError:
        # Race condition: another request created a subscription for the same
        # user+plan between our SELECT and INSERT. Roll back and retry once.
        await db.rollback()
        logger.warning(
            "Subscription insert race: user=%s plan=%s — retrying",
            user_id, plan.id,
        )
        retry_stmt = (
            select(UserSubscription)
            .where(
                and_(
                    UserSubscription.user_id == user_id,
                    UserSubscription.plan_id == plan.id,
                )
            )
            .with_for_update()
        )
        retry_result = await db.execute(retry_stmt)
        existing = retry_result.scalar_one_or_none()
        if existing:
            existing.status = "active"
            existing.current_period_start = now
            existing.current_period_end = _compute_period_end(now, plan)
            existing.cancel_at_period_end = False
            existing.livemode = livemode
            existing.updated_at = now
            subscription = existing
            await db.flush()
        else:
            raise

    return {
        "subscription_id": subscription.id,
        "plan_name": plan.name,
        "status": subscription.status,
        "current_period_start": subscription.current_period_start.isoformat() if subscription.current_period_start else None,
        "current_period_end": subscription.current_period_end.isoformat() if subscription.current_period_end else None,
    }


async def deactivate_subscription(
    db: AsyncSession,
    *,
    user_id: int,
    plan: SubscriptionPlan,
    new_status: str,
) -> bool:
    """Mark a user's subscription as expired or revoked.

    Returns True if a subscription was found and updated, False otherwise.
    """
    now = datetime.now(timezone.utc)

    stmt = (
        select(UserSubscription)
        .where(
            and_(
                UserSubscription.user_id == user_id,
                UserSubscription.plan_id == plan.id,
            )
        )
        .with_for_update()
    )
    result = await db.execute(stmt)
    subscription = result.scalar_one_or_none()

    if not subscription or subscription.status in (new_status, "revoked"):
        return False

    subscription.status = new_status
    subscription.updated_at = now
    if new_status == "revoked":
        subscription.canceled_at = now
    elif new_status == "expired":
        subscription.cancel_at_period_end = False

    logger.info(
        "Subscription %s: user=%s plan=%s status=%s",
        new_status, user_id, plan.id, new_status,
    )
    await db.flush()
    return True


async def update_subscription_renewal_status(
    db: AsyncSession,
    *,
    user_id: int,
    plan: SubscriptionPlan,
    cancel_at_period_end: bool,
) -> bool:
    """Update cancel_at_period_end flag on a user's subscription.

    Used for Apple DID_FAIL_TO_RENEW and DID_CHANGE_RENEWAL_STATUS notifications.
    Returns True if a subscription was found and updated, False otherwise.
    """
    now = datetime.now(timezone.utc)

    stmt = (
        select(UserSubscription)
        .where(
            and_(
                UserSubscription.user_id == user_id,
                UserSubscription.plan_id == plan.id,
            )
        )
        .with_for_update()
    )
    result = await db.execute(stmt)
    subscription = result.scalar_one_or_none()

    if not subscription:
        return False

    subscription.cancel_at_period_end = cancel_at_period_end
    subscription.updated_at = now
    if cancel_at_period_end:
        subscription.canceled_at = now
    else:
        subscription.canceled_at = None

    logger.info(
        "Subscription renewal status updated: user=%s plan=%s cancel_at_period_end=%s",
        user_id, plan.id, cancel_at_period_end,
    )
    await db.flush()
    return True
