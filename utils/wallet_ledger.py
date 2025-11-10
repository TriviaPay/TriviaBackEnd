"""
Wallet Ledger Service

Provides atomic wallet balance updates using a double-entry ledger system.
All amounts are stored in minor units (cents) to avoid floating-point precision issues.
"""
import logging
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert
from models import WalletLedger, UserWalletBalance, User
from datetime import datetime

logger = logging.getLogger(__name__)


def validate_currency(currency: str) -> bool:
    """Validate currency code format (3-letter ISO code)."""
    if not currency or len(currency) < 3 or len(currency) > 10:
        return False
    return currency.isalpha()


def add_ledger_entry(
    db: Session,
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
    Add a ledger entry and atomically update wallet balance.
    
    Uses row-level locking (SELECT FOR UPDATE NOWAIT) to prevent race conditions.
    
    Args:
        db: Database session
        user_id: User account ID
        currency: Currency code (e.g., 'usd')
        delta_minor: Amount change in minor units (can be negative)
        kind: Type of entry (deposit/withdraw/refund/fee/adjustment/dispute_hold/dispute_release)
        external_ref_type: Type of external reference (payment_intent/charge/refund/etc)
        external_ref_id: External reference ID
        event_id: Stripe event ID (for idempotency)
        idempotency_key: Custom idempotency key
        livemode: Whether this is a live mode transaction
        
    Returns:
        New balance in minor units
        
    Raises:
        ValueError: If currency is invalid or delta is zero
        Exception: If idempotency check fails or balance would go negative
    """
    if delta_minor == 0:
        raise ValueError("delta_minor cannot be zero")
    
    if not validate_currency(currency):
        raise ValueError(f"Invalid currency code: {currency}")
    
    # Check idempotency if event_id or idempotency_key provided
    if event_id:
        existing = db.query(WalletLedger).filter(WalletLedger.event_id == event_id).first()
        if existing:
            logger.info(f"Duplicate event_id {event_id} detected, returning existing balance")
            return existing.balance_after_minor
    
    if idempotency_key:
        existing = db.query(WalletLedger).filter(WalletLedger.idempotency_key == idempotency_key).first()
        if existing:
            logger.info(f"Duplicate idempotency_key {idempotency_key} detected, returning existing balance")
            return existing.balance_after_minor
    
    # Per-object idempotency check: ensure only one ledger entry per (external_ref_type, external_ref_id, kind)
    if external_ref_type and external_ref_id and kind:
        existing = db.query(WalletLedger).filter(
            WalletLedger.external_ref_type == external_ref_type,
            WalletLedger.external_ref_id == external_ref_id,
            WalletLedger.kind == kind,
            WalletLedger.user_id == user_id,
            WalletLedger.currency == currency
        ).first()
        if existing:
            logger.info(
                f"Duplicate ledger entry detected: {external_ref_type}/{external_ref_id}/{kind} "
                f"for user {user_id}/{currency}, returning existing balance"
            )
            return existing.balance_after_minor
    
    # Use row-level locking to prevent concurrent updates
    # Lock the user's wallet balance row for this currency
    balance_row = db.query(UserWalletBalance).filter(
        UserWalletBalance.user_id == user_id,
        UserWalletBalance.currency == currency
    ).with_for_update(nowait=True).first()
    
    # Calculate new balance
    if balance_row:
        current_balance = balance_row.balance_minor
    else:
        # First transaction for this user/currency, start at 0
        current_balance = 0
    
    new_balance = current_balance + delta_minor
    
    # Prevent negative balances (except for adjustments and dispute holds)
    if new_balance < 0 and kind not in ('adjustment', 'dispute_hold'):
        raise ValueError(f"Insufficient balance. Current: {current_balance}, Attempted: {delta_minor}")
    
    # Create ledger entry
    ledger_entry = WalletLedger(
        user_id=user_id,
        currency=currency,
        delta_minor=delta_minor,
        balance_after_minor=new_balance,
        kind=kind,
        external_ref_type=external_ref_type,
        external_ref_id=external_ref_id,
        event_id=event_id,
        idempotency_key=idempotency_key,
        livemode=livemode,
        created_at=datetime.utcnow()
    )
    
    db.add(ledger_entry)
    
    # Update or create balance cache
    if balance_row:
        balance_row.balance_minor = new_balance
        balance_row.last_recalculated_at = datetime.utcnow()
    else:
        balance_row = UserWalletBalance(
            user_id=user_id,
            currency=currency,
            balance_minor=new_balance,
            last_recalculated_at=datetime.utcnow()
        )
        db.add(balance_row)
    
    # Also update users.wallet_balance_minor for backward compatibility (USD only)
    if currency == 'usd':
        user = db.query(User).filter(User.account_id == user_id).first()
        if user:
            user.wallet_balance_minor = new_balance
            user.wallet_currency = currency
            user.last_wallet_update = datetime.utcnow()
    
    db.flush()  # Flush to get the ledger entry ID
    
    logger.info(
        f"Ledger entry created: user={user_id}, currency={currency}, "
        f"delta={delta_minor}, balance={new_balance}, kind={kind}"
    )
    
    return new_balance


def get_balance(db: Session, user_id: int, currency: str = 'usd') -> int:
    """
    Get current wallet balance for a user and currency.
    
    Args:
        db: Database session
        user_id: User account ID
        currency: Currency code (default: 'usd')
        
    Returns:
        Balance in minor units (0 if no balance found)
    """
    balance_row = db.query(UserWalletBalance).filter(
        UserWalletBalance.user_id == user_id,
        UserWalletBalance.currency == currency
    ).first()
    
    if balance_row:
        return balance_row.balance_minor
    
    # Fallback: calculate from ledger if cache missing
    return recalculate_balance(db, user_id, currency)


def recalculate_balance(db: Session, user_id: int, currency: str = 'usd') -> int:
    """
    Recalculate balance from ledger entries (for reconciliation).
    
    Args:
        db: Database session
        user_id: User account ID
        currency: Currency code
        
    Returns:
        Calculated balance in minor units
    """
    result = db.query(func.sum(WalletLedger.delta_minor)).filter(
        WalletLedger.user_id == user_id,
        WalletLedger.currency == currency
    ).scalar()
    
    calculated_balance = result if result is not None else 0
    
    # Update cache
    balance_row = db.query(UserWalletBalance).filter(
        UserWalletBalance.user_id == user_id,
        UserWalletBalance.currency == currency
    ).first()
    
    if balance_row:
        balance_row.balance_minor = calculated_balance
        balance_row.last_recalculated_at = datetime.utcnow()
    else:
        balance_row = UserWalletBalance(
            user_id=user_id,
            currency=currency,
            balance_minor=calculated_balance,
            last_recalculated_at=datetime.utcnow()
        )
        db.add(balance_row)
    
    # Also update users.wallet_balance_minor for backward compatibility (USD only)
    if currency == 'usd':
        user = db.query(User).filter(User.account_id == user_id).first()
        if user:
            user.wallet_balance_minor = calculated_balance
            user.wallet_currency = currency
    
    return calculated_balance


def get_ledger_entries(
    db: Session,
    user_id: int,
    currency: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
) -> list:
    """
    Get ledger entries for a user with optional filters.
    
    Args:
        db: Database session
        user_id: User account ID
        currency: Optional currency filter
        kind: Optional kind filter
        limit: Maximum number of entries
        offset: Offset for pagination
        
    Returns:
        List of WalletLedger entries
    """
    query = db.query(WalletLedger).filter(WalletLedger.user_id == user_id)
    
    if currency:
        query = query.filter(WalletLedger.currency == currency)
    
    if kind:
        query = query.filter(WalletLedger.kind == kind)
    
    return query.order_by(WalletLedger.created_at.desc()).offset(offset).limit(limit).all()

