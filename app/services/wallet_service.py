"""
Wallet Service - Handles wallet balance adjustments and queries
"""
import logging
from datetime import datetime, date
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload
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
    livemode: bool = False
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
    supported_currencies = ['usd', 'eur', 'gbp', 'cad', 'aud']
    if currency not in supported_currencies:
        raise ValueError(f"Unsupported currency: {currency}. Supported: {', '.join(supported_currencies)}")
    
    # Check idempotency if event_id or idempotency_key provided
    if event_id:
        stmt = select(WalletTransaction).where(WalletTransaction.event_id == event_id)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            logger.info(f"Duplicate event_id {event_id} detected, returning existing balance")
            # Get current balance from user
            user_stmt = select(User).where(User.account_id == user_id)
            user_result = await db.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            if user:
                return user.wallet_balance_minor or 0
            return 0
    
    if idempotency_key:
        stmt = select(WalletTransaction).where(WalletTransaction.idempotency_key == idempotency_key)
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
                WalletTransaction.currency == currency
            )
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            logger.info(f"Duplicate ledger entry detected: {external_ref_type}/{external_ref_id}/{kind}")
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
    current_currency = (user.wallet_currency or 'usd').lower()
    if current_currency != currency:
        raise ValueError(f"Currency mismatch: user wallet is {current_currency}, but operation is for {currency}. Cross-currency operations are not allowed.")
    
    # Calculate new balance
    current_balance = user.wallet_balance_minor or 0
    new_balance = current_balance + delta_minor
    
    # Prevent negative balances (except for adjustments and dispute holds)
    if new_balance < 0 and kind not in ('adjustment', 'dispute_hold'):
        raise ValueError(f"Insufficient balance. Current: {current_balance}, Attempted: {delta_minor}")
    
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
        created_at=datetime.utcnow()
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
    db: AsyncSession,
    user_id: int,
    currency: str = 'usd'
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
    supported_currencies = ['usd', 'eur', 'gbp', 'cad', 'aud']
    if currency not in supported_currencies:
        raise ValueError(f"Unsupported currency: {currency}. Supported: {', '.join(supported_currencies)}")
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


async def get_daily_instant_withdrawal_count(
    db: AsyncSession,
    user_id: int,
    withdrawal_date: date
) -> int:
    """
    Get total amount of instant withdrawals for a user on a specific date.
    
    Args:
        db: Async database session
        user_id: User account ID
        withdrawal_date: Date to check
        
    Returns:
        Total amount in minor units
    """
    from app.models.wallet import WithdrawalRequest
    
    start_of_day = datetime.combine(withdrawal_date, datetime.min.time())
    end_of_day = datetime.combine(withdrawal_date, datetime.max.time())
    
    stmt = select(func.sum(WithdrawalRequest.amount_minor)).where(
        and_(
            WithdrawalRequest.user_id == user_id,
            WithdrawalRequest.type == 'instant',
            WithdrawalRequest.status.in_(['processing', 'paid']),
            WithdrawalRequest.requested_at >= start_of_day,
            WithdrawalRequest.requested_at <= end_of_day
        )
    )
    result = await db.execute(stmt)
    total = result.scalar() or 0
    
    return total


def calculate_withdrawal_fee(amount_minor: int, withdrawal_type: str) -> int:
    """
    Calculate withdrawal fee based on amount and type.
    
    Args:
        amount_minor: Amount in minor units
        withdrawal_type: 'standard' or 'instant'
        
    Returns:
        Fee in minor units
    """
    if withdrawal_type == 'instant':
        # Instant withdrawal fee: 2% or minimum $0.50 (50 cents)
        fee_percent = 0.02
        fee = int(amount_minor * fee_percent)
        min_fee = 50  # $0.50 minimum
        return max(fee, min_fee)
    else:
        # Standard withdrawal: no fee
        return 0

