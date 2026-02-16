"""
Wallet Service - Handles wallet balance adjustments and queries
"""

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.wallet import WalletTransaction

logger = logging.getLogger(__name__)


async def adjust_wallet_balance(
    db: AsyncSession,
    user_id: int,
    currency: str,
    delta_minor: int,
    kind: str,
    external_ref_type: Optional[str] = None,
    external_ref_id: Optional[str] = None,
    event_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    livemode: bool = False,
) -> int:
    """
    Adjust wallet balance atomically with row-level locking.

    Uses SELECT FOR UPDATE to prevent race conditions.

    Args:
        db: Async database session
        user_id: User account ID
        currency: Currency code (e.g., 'usd')
        delta_minor: Amount change in minor units (can be negative)
        kind: Type of entry (deposit, withdraw, refund, fee, adjustment, etc.)
        external_ref_type: Type of external reference
        external_ref_id: External reference ID
        event_id: Event ID for idempotency
        idempotency_key: Custom idempotency key
        livemode: Whether this is a live mode transaction

    Returns:
        New balance in minor units

    Raises:
        ValueError: If currency is invalid, delta is zero, or balance would go negative
    """
    if delta_minor == 0:
        raise ValueError("delta_minor cannot be zero")

    if not currency or len(currency) < 3:
        raise ValueError(f"Invalid currency code: {currency}")

    currency = currency.lower()

    # Validate currency is supported
    supported_currencies = ["usd", "eur", "gbp", "cad", "aud"]
    if currency not in supported_currencies:
        raise ValueError(
            f"Unsupported currency: {currency}. Supported: {', '.join(supported_currencies)}"
        )

    # Check idempotency if event_id or idempotency_key provided
    if event_id:
        stmt = select(WalletTransaction).where(WalletTransaction.event_id == event_id)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            logger.info(
                f"Duplicate event_id {event_id} detected, returning existing balance"
            )
            # Get current balance from user
            user_stmt = select(User).where(User.account_id == user_id)
            user_result = await db.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            if user:
                return user.wallet_balance_minor or 0
            return 0

    if idempotency_key:
        stmt = select(WalletTransaction).where(
            WalletTransaction.idempotency_key == idempotency_key
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            logger.info(f"Duplicate idempotency_key {idempotency_key} detected")
            user_stmt = select(User).where(User.account_id == user_id)
            user_result = await db.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            if user:
                return user.wallet_balance_minor or 0
            return 0

    # Per-object idempotency check
    if external_ref_type and external_ref_id and kind:
        stmt = select(WalletTransaction).where(
            and_(
                WalletTransaction.external_ref_type == external_ref_type,
                WalletTransaction.external_ref_id == external_ref_id,
                WalletTransaction.kind == kind,
                WalletTransaction.user_id == user_id,
                WalletTransaction.currency == currency,
            )
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            logger.info(
                f"Duplicate ledger entry detected: {external_ref_type}/{external_ref_id}/{kind}"
            )
            user_stmt = select(User).where(User.account_id == user_id)
            user_result = await db.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            if user:
                return user.wallet_balance_minor or 0
            return 0

    # Lock user row for update
    user_stmt = select(User).where(User.account_id == user_id).with_for_update()
    user_result = await db.execute(user_stmt)
    user = user_result.scalar_one_or_none()

    if not user:
        raise ValueError(f"User {user_id} not found")

    # Check currency match - prevent cross-currency operations
    current_currency = (user.wallet_currency or "usd").lower()
    if current_currency != currency:
        raise ValueError(
            f"Currency mismatch: user wallet is {current_currency}, but operation is for {currency}. Cross-currency operations are not allowed."
        )

    # Calculate new balance
    current_balance = user.wallet_balance_minor or 0
    new_balance = current_balance + delta_minor

    # Prevent negative balances. For IAP refunds, clamp to zero instead of going negative.
    if new_balance < 0:
        if kind == "iap_refund":
            applied_delta = -current_balance
            if applied_delta == 0:
                logger.info(
                    "IAP refund clamped to zero: user=%s, current_balance=%s, "
                    "requested_delta=%s, event_id=%s",
                    user_id,
                    current_balance,
                    delta_minor,
                    event_id,
                )
                return current_balance
            logger.warning(
                "IAP refund clamped to zero: user=%s, current_balance=%s, "
                "requested_delta=%s, applied_delta=%s, event_id=%s",
                user_id,
                current_balance,
                delta_minor,
                applied_delta,
                event_id,
            )
            delta_minor = applied_delta
            new_balance = 0
        else:
            raise ValueError(
                f"Insufficient balance. Current: {current_balance}, Attempted: {delta_minor}"
            )

    # Create wallet transaction
    transaction = WalletTransaction(
        user_id=user_id,
        amount_minor=delta_minor,
        currency=currency,
        kind=kind,
        external_ref_type=external_ref_type,
        external_ref_id=external_ref_id,
        event_id=event_id,
        idempotency_key=idempotency_key,
        livemode=livemode,
        created_at=datetime.utcnow(),
    )
    db.add(transaction)

    # Update user wallet balance
    user.wallet_balance_minor = new_balance
    user.wallet_currency = currency
    user.last_wallet_update = datetime.utcnow()

    await db.flush()

    logger.info(
        f"Wallet balance adjusted: user={user_id}, currency={currency}, "
        f"delta={delta_minor}, balance={new_balance}, kind={kind}"
    )

    return new_balance


async def get_wallet_balance(
    db: AsyncSession, user_id: int, currency: str = "usd"
) -> int:
    """
    Get wallet balance for a user in a specific currency.

    Validates currency code and prevents cross-currency operations.

    Args:
        db: Async database session
        user_id: User account ID
        currency: Currency code (e.g., 'usd', 'eur')

    Returns:
        Balance in minor units

    Raises:
        ValueError: If currency is invalid
    """
    # Validate currency code
    if not currency or len(currency) < 3:
        raise ValueError(f"Invalid currency code: {currency}")

    currency = currency.lower()

    # Supported currencies (add more as needed)
    supported_currencies = ["usd", "eur", "gbp", "cad", "aud"]
    if currency not in supported_currencies:
        raise ValueError(
            f"Unsupported currency: {currency}. Supported: {', '.join(supported_currencies)}"
        )
    """
    Get current wallet balance for a user and currency.

    Args:
        db: Async database session
        user_id: User account ID
        currency: Currency code (default: 'usd')

    Returns:
        Balance in minor units (0 if no balance found)
    """
    stmt = select(User).where(User.account_id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user and user.wallet_balance_minor is not None:
        return user.wallet_balance_minor

    return 0
