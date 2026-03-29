"""
Product Pricing Service - Provides product price lookup from database
"""

import logging
from typing import Any, Dict

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.products import Avatar, Badge, Frame, GemPackageConfig
from models import SubscriptionPlan

logger = logging.getLogger(__name__)


async def get_price_minor_for_product_id(db: AsyncSession, product_id: str) -> int:
    """
    Given a logical product_id like 'GP001', 'FR001', 'BD001', 'AV001', look up the
    correct price in cents from the database tables (gem packages, frames,
    avatars, badges, etc.).

    Args:
        db: Async database session
        product_id: Product ID (e.g., "AV001", "GP001", "FR001", "BD001")

    Returns:
        Price in minor units (cents)

    Raises:
        HTTPException(400, ...) if product_id is unknown or price_minor is None

    Note:
        Do NOT trust the client for price; always use the DB.
    """
    # Try to find product in avatars (AV prefix)
    if product_id.startswith("AV"):
        stmt = select(Avatar).where(Avatar.product_id == product_id)
        result = await db.execute(stmt)
        product = result.scalar_one_or_none()
        if product and product.price_minor is not None:
            return product.price_minor

    # Try to find product in frames (FR prefix)
    elif product_id.startswith("FR"):
        stmt = select(Frame).where(Frame.product_id == product_id)
        result = await db.execute(stmt)
        product = result.scalar_one_or_none()
        if product and product.price_minor is not None:
            return product.price_minor

    # Try to find product in gem_package_config (GP prefix)
    elif product_id.startswith("GP"):
        stmt = select(GemPackageConfig).where(GemPackageConfig.product_id == product_id)
        result = await db.execute(stmt)
        product = result.scalar_one_or_none()
        if product and product.price_minor is not None:
            return product.price_minor

    # Try to find product in badges (BD prefix)
    elif product_id.startswith("BD"):
        try:
            stmt = select(Badge).where(Badge.product_id == product_id)
            result = await db.execute(stmt)
            product = result.scalar_one_or_none()
            if product and product.price_minor is not None:
                return product.price_minor
        except Exception:
            pass

    # If no prefix matches, try all tables (fallback)
    else:
        for model in (Avatar, Frame, GemPackageConfig):
            stmt = select(model).where(model.product_id == product_id)
            result = await db.execute(stmt)
            product = result.scalar_one_or_none()
            if product and product.price_minor is not None:
                return product.price_minor
        # Try badges last (table may not exist)
        try:
            stmt = select(Badge).where(Badge.product_id == product_id)
            result = await db.execute(stmt)
            product = result.scalar_one_or_none()
            if product and product.price_minor is not None:
                return product.price_minor
        except Exception:
            pass

    # Product not found or price_minor is None
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Product ID '{product_id}' not found in product tables or price_minor is not set",
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

    if product_id.startswith("AV"):
        stmt = select(Avatar).where(Avatar.product_id == product_id)
        result = await db.execute(stmt)
        product = result.scalar_one_or_none()
    elif product_id.startswith("FR"):
        stmt = select(Frame).where(Frame.product_id == product_id)
        result = await db.execute(stmt)
        product = result.scalar_one_or_none()
    elif product_id.startswith("GP"):
        stmt = select(GemPackageConfig).where(GemPackageConfig.product_id == product_id)
        result = await db.execute(stmt)
        product = result.scalar_one_or_none()
    elif product_id.startswith("BD"):
        try:
            stmt = select(Badge).where(Badge.product_id == product_id)
            result = await db.execute(stmt)
            product = result.scalar_one_or_none()
        except Exception:
            logger.warning("Badge table query failed for product_id %s", product_id)
    else:
        # Fallback: try all tables (skip Badge if table doesn't exist)
        for model in (Avatar, Frame, GemPackageConfig, Badge):
            try:
                stmt = select(model).where(model.product_id == product_id)
                result = await db.execute(stmt)
                product = result.scalar_one_or_none()
                if product:
                    break
            except Exception:
                continue

    if not product or product.price_minor is None:
        # Try subscription plans (by apple, google, or stripe product ID)
        sub_stmt = select(SubscriptionPlan).where(
            (SubscriptionPlan.apple_product_id == product_id)
            | (SubscriptionPlan.google_product_id == product_id)
            | (SubscriptionPlan.stripe_product_id == product_id)
            | (SubscriptionPlan.paypal_product_id == product_id)
        )
        sub_result = await db.execute(sub_stmt)
        sub_plan = sub_result.scalar_one_or_none()
        if sub_plan and sub_plan.unit_amount_minor is not None:
            if sub_plan.unit_amount_minor <= 0:
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

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product ID '{product_id}' not found in product tables or price_minor is not set",
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
    }
