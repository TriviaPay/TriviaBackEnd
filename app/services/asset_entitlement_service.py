"""
Asset Entitlement Service — async grant/revoke for avatars & frames.

Uses the async UserAvatar/UserFrame models from app.models.products.
Idempotent: duplicate grants succeed with already_owned=True.
"""

import logging
from datetime import datetime
from typing import Any, Dict

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.products import Avatar, Frame, UserAvatar, UserFrame
from app.models.user import User

logger = logging.getLogger(__name__)


async def check_already_owned(
    db: AsyncSession, *, user_id: int, product_id: str
) -> bool:
    """Return True if the user already owns this avatar or frame."""
    if product_id.startswith("AV"):
        result = await db.execute(
            select(Avatar).where(Avatar.product_id == product_id)
        )
        avatar = result.scalar_one_or_none()
        if not avatar:
            return False
        existing = await db.execute(
            select(UserAvatar).where(
                UserAvatar.user_id == user_id,
                UserAvatar.avatar_id == avatar.id,
            )
        )
        return existing.scalar_one_or_none() is not None

    elif product_id.startswith("FR"):
        result = await db.execute(
            select(Frame).where(Frame.product_id == product_id)
        )
        frame = result.scalar_one_or_none()
        if not frame:
            return False
        existing = await db.execute(
            select(UserFrame).where(
                UserFrame.user_id == user_id,
                UserFrame.frame_id == frame.id,
            )
        )
        return existing.scalar_one_or_none() is not None

    return False


async def grant_asset(
    db: AsyncSession, *, user_id: int, product_id: str
) -> Dict[str, Any]:
    """
    Grant an avatar or frame to a user by product_id.

    Returns {"asset_type", "asset_id", "already_owned"}.
    Idempotent: if already owned, returns success with already_owned=True.
    """
    if product_id.startswith("AV"):
        result = await db.execute(
            select(Avatar).where(Avatar.product_id == product_id)
        )
        avatar = result.scalar_one_or_none()
        if not avatar:
            raise ValueError(f"Avatar not found: {product_id}")

        existing = await db.execute(
            select(UserAvatar).where(
                UserAvatar.user_id == user_id,
                UserAvatar.avatar_id == avatar.id,
            )
        )
        if existing.scalar_one_or_none():
            return {"asset_type": "avatar", "asset_id": avatar.id, "already_owned": True}

        db.add(
            UserAvatar(
                user_id=user_id,
                avatar_id=avatar.id,
                purchase_date=datetime.utcnow(),
            )
        )
        logger.info("Granted avatar %s to user %s", avatar.id, user_id)
        return {"asset_type": "avatar", "asset_id": avatar.id, "already_owned": False}

    elif product_id.startswith("FR"):
        result = await db.execute(
            select(Frame).where(Frame.product_id == product_id)
        )
        frame = result.scalar_one_or_none()
        if not frame:
            raise ValueError(f"Frame not found: {product_id}")

        existing = await db.execute(
            select(UserFrame).where(
                UserFrame.user_id == user_id,
                UserFrame.frame_id == frame.id,
            )
        )
        if existing.scalar_one_or_none():
            return {"asset_type": "frame", "asset_id": frame.id, "already_owned": True}

        db.add(
            UserFrame(
                user_id=user_id,
                frame_id=frame.id,
                purchase_date=datetime.utcnow(),
            )
        )
        logger.info("Granted frame %s to user %s", frame.id, user_id)
        return {"asset_type": "frame", "asset_id": frame.id, "already_owned": False}

    else:
        raise ValueError(f"Unknown non-consumable product_id prefix: {product_id}")


async def revoke_asset(
    db: AsyncSession, *, user_id: int, product_id: str
) -> Dict[str, Any]:
    """
    Revoke an avatar or frame from a user (refund path).

    Returns {"asset_type", "asset_id", "was_owned"}.
    If user had this asset selected, clears the selection.
    """
    if product_id.startswith("AV"):
        result = await db.execute(
            select(Avatar).where(Avatar.product_id == product_id)
        )
        avatar = result.scalar_one_or_none()
        if not avatar:
            raise ValueError(f"Avatar not found: {product_id}")

        del_result = await db.execute(
            delete(UserAvatar).where(
                UserAvatar.user_id == user_id,
                UserAvatar.avatar_id == avatar.id,
            )
        )
        was_owned = del_result.rowcount > 0

        if was_owned:
            # Clear selection if the revoked avatar was active
            user_result = await db.execute(
                select(User).where(User.account_id == user_id)
            )
            user = user_result.scalar_one_or_none()
            if user and user.selected_avatar_id == avatar.id:
                user.selected_avatar_id = None

        logger.info(
            "Revoked avatar %s from user %s (was_owned=%s)",
            avatar.id, user_id, was_owned,
        )
        return {"asset_type": "avatar", "asset_id": avatar.id, "was_owned": was_owned}

    elif product_id.startswith("FR"):
        result = await db.execute(
            select(Frame).where(Frame.product_id == product_id)
        )
        frame = result.scalar_one_or_none()
        if not frame:
            raise ValueError(f"Frame not found: {product_id}")

        del_result = await db.execute(
            delete(UserFrame).where(
                UserFrame.user_id == user_id,
                UserFrame.frame_id == frame.id,
            )
        )
        was_owned = del_result.rowcount > 0

        if was_owned:
            user_result = await db.execute(
                select(User).where(User.account_id == user_id)
            )
            user = user_result.scalar_one_or_none()
            if user and user.selected_frame_id == frame.id:
                user.selected_frame_id = None

        logger.info(
            "Revoked frame %s from user %s (was_owned=%s)",
            frame.id, user_id, was_owned,
        )
        return {"asset_type": "frame", "asset_id": frame.id, "was_owned": was_owned}

    else:
        raise ValueError(f"Unknown non-consumable product_id prefix: {product_id}")
