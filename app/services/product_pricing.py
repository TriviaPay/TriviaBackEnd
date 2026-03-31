"""
Product Pricing Service - Provides product price lookup from database
"""

import logging
from typing import Any, Dict

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.products import Avatar, Frame, GemPackageConfig
from models import SubscriptionPlan

logger = logging.getLogger(__name__)

# Badge model import — table may not exist in production
try:
    from app.models.products import Badge

    _BADGE_AVAILABLE = True
except Exception:
    _BADGE_AVAILABLE = False

# Prefix → model mapping for fast-path lookups.
# DB uses: G001 (gems), A001 (avatars), FR001 (frames), BD001 (badges)
_PREFIX_MODEL_MAP = {
    "G": GemPackageConfig,
    "A": Avatar,
    "FR": Frame,
}


async def _safe_badge_lookup(db: AsyncSession, product_id: str):
    """Query the badges table, returning None if table doesn't exist."""
    if not _BADGE_AVAILABLE:
        return None
    try:
        stmt = select(Badge).where(Badge.product_id == product_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
    except Exception:
        # Badge table doesn't exist — rollback to clear failed transaction
        await db.rollback()
        return None


async def _lookup_subscription(db: AsyncSession, product_id: str):
    """Look up a subscription plan by product_id or platform-specific IDs."""
    sub_stmt = select(SubscriptionPlan).where(
        (SubscriptionPlan.product_id == product_id)
        | (SubscriptionPlan.apple_product_id == product_id)
        | (SubscriptionPlan.google_product_id == product_id)
        | (SubscriptionPlan.stripe_product_id == product_id)
        | (SubscriptionPlan.paypal_product_id == product_id)
    )
    sub_result = await db.execute(sub_stmt)
    return sub_result.scalar_one_or_none()


def _resolve_prefix(product_id: str):
    """Return the model class for a product_id prefix, or None."""
    # Check longer prefixes first (FR, BD) then single-char (G, A)
    for prefix, model in _PREFIX_MODEL_MAP.items():
        if product_id.startswith(prefix):
            return model
    return None


async def get_price_minor_for_product_id(db: AsyncSession, product_id: str) -> int:
    """
    Look up the correct price in cents from the database.

    Args:
        db: Async database session
        product_id: Product ID (e.g., "A001", "G001", "FR001", "SUB001")

    Returns:
        Price in minor units (cents)

    Raises:
        HTTPException(400) if product_id is unknown or price_minor is None
    """
    # Subscription fast path
    if product_id.startswith("SUB"):
        sub = await _lookup_subscription(db, product_id)
        if sub and sub.unit_amount_minor is not None:
            return sub.unit_amount_minor

    # Badge fast path
    elif product_id.startswith("BD"):
        product = await _safe_badge_lookup(db, product_id)
        if product and product.price_minor is not None:
            return product.price_minor

    else:
        # Try prefix-based model lookup
        model = _resolve_prefix(product_id)
        if model:
            stmt = select(model).where(model.product_id == product_id)
            result = await db.execute(stmt)
            product = result.scalar_one_or_none()
            if product and product.price_minor is not None:
                return product.price_minor
        else:
            # Unknown prefix — try all tables
            for m in (GemPackageConfig, Avatar, Frame):
                stmt = select(m).where(m.product_id == product_id)
                result = await db.execute(stmt)
                product = result.scalar_one_or_none()
                if product and product.price_minor is not None:
                    return product.price_minor
            product = await _safe_badge_lookup(db, product_id)
            if product and product.price_minor is not None:
                return product.price_minor
            # Last resort: subscription by platform ID
            sub = await _lookup_subscription(db, product_id)
            if sub and sub.unit_amount_minor is not None:
                return sub.unit_amount_minor

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Product ID '{product_id}' not found or has no price set",
    )


async def get_product_info(db: AsyncSession, product_id: str) -> Dict[str, Any]:
    """
    Return product metadata (price and type) for a product_id.

    Returns:
        {
            "product_id": str,
            "price_minor": int,
            "product_type": str
        }
    """
    product = None

    # Subscription fast path
    if product_id.startswith("SUB"):
        sub = await _lookup_subscription(db, product_id)
        if sub:
            return _build_subscription_response(product_id, sub)

    # Badge fast path
    elif product_id.startswith("BD"):
        product = await _safe_badge_lookup(db, product_id)

    else:
        # Try prefix-based model lookup
        model = _resolve_prefix(product_id)
        if model:
            stmt = select(model).where(model.product_id == product_id)
            result = await db.execute(stmt)
            product = result.scalar_one_or_none()
        else:
            # Unknown prefix — try all tables
            for m in (GemPackageConfig, Avatar, Frame):
                stmt = select(m).where(m.product_id == product_id)
                result = await db.execute(stmt)
                product = result.scalar_one_or_none()
                if product:
                    break
            if not product:
                product = await _safe_badge_lookup(db, product_id)

    if not product or product.price_minor is None:
        # Try subscription plans (by product_id or platform-specific IDs)
        sub = await _lookup_subscription(db, product_id)
        if sub:
            return _build_subscription_response(product_id, sub)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product ID '{product_id}' not found or has no price set",
        )

    if product.price_minor <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product '{product_id}' has invalid price",
        )

    product_type = getattr(product, "product_type", None) or "consumable"

    return {
        "product_id": product_id,
        "price_minor": product.price_minor,
        "product_type": product_type,
        "product_name": getattr(product, "description", None) or product_id,
        "gems_amount": getattr(product, "gems_amount", None),
        "plan_id": None,
        "stripe_price_id": None,
        "paypal_plan_id": getattr(product, "paypal_plan_id", None),
    }


def _build_subscription_response(product_id: str, sub_plan) -> Dict[str, Any]:
    """Build a standardised product-info dict for a SubscriptionPlan."""
    if sub_plan.unit_amount_minor is None or sub_plan.unit_amount_minor <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product '{product_id}' has invalid price",
        )
    return {
        "product_id": product_id,
        "price_minor": sub_plan.unit_amount_minor,
        "product_type": "subscription",
        "product_name": sub_plan.name,
        "plan_id": sub_plan.id,
        "stripe_price_id": getattr(sub_plan, "stripe_price_id", None),
        "paypal_plan_id": getattr(sub_plan, "paypal_plan_id", None),
        "gems_amount": None,
    }
