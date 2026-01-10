"""
Product Pricing Service - Provides product price lookup from database
"""

import logging

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.products import Avatar, Badge, Frame, GemPackageConfig

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
        stmt = select(Badge).where(Badge.product_id == product_id)
        result = await db.execute(stmt)
        product = result.scalar_one_or_none()
        if product and product.price_minor is not None:
            return product.price_minor

    # If no prefix matches, try all tables (fallback)
    else:
        # Try avatars
        stmt = select(Avatar).where(Avatar.product_id == product_id)
        result = await db.execute(stmt)
        product = result.scalar_one_or_none()
        if product and product.price_minor is not None:
            return product.price_minor

        # Try frames
        stmt = select(Frame).where(Frame.product_id == product_id)
        result = await db.execute(stmt)
        product = result.scalar_one_or_none()
        if product and product.price_minor is not None:
            return product.price_minor

        # Try gem_package_config
        stmt = select(GemPackageConfig).where(GemPackageConfig.product_id == product_id)
        result = await db.execute(stmt)
        product = result.scalar_one_or_none()
        if product and product.price_minor is not None:
            return product.price_minor

        # Try badges
        stmt = select(Badge).where(Badge.product_id == product_id)
        result = await db.execute(stmt)
        product = result.scalar_one_or_none()
        if product and product.price_minor is not None:
            return product.price_minor

    # Product not found or price_minor is None
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Product ID '{product_id}' not found in product tables or price_minor is not set",
    )
