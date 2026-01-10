"""
Stripe Reconciliation Service

Compares internal wallet ledger balances with Stripe's balance transactions
to detect discrepancies and ensure accounting accuracy.
"""

import logging
from datetime import date, datetime
from typing import Dict, List, Optional

import stripe
from sqlalchemy.orm import Session

from models import StripeReconciliationSnapshot, WalletLedger

logger = logging.getLogger(__name__)


def get_stripe_balance(currency: str = "usd") -> Dict[str, int]:
    """
    Get Stripe balance from API.

    Args:
        currency: Currency code to check

    Returns:
        Dict with 'available' and 'pending' amounts in minor units
    """
    try:
        balance = stripe.Balance.retrieve()

        # Find balance for the specified currency
        available = 0
        pending = 0

        for balance_item in balance.available:
            if balance_item.currency.lower() == currency.lower():
                available = balance_item.amount
                break

        for balance_item in balance.pending:
            if balance_item.currency.lower() == currency.lower():
                pending = balance_item.amount
                break

        return {"available_minor": available, "pending_minor": pending}
    except Exception as e:
        logger.error(f"Error fetching Stripe balance: {str(e)}")
        raise


def calculate_platform_balance(db: Session, currency: str = "usd") -> Dict[str, int]:
    """
    Calculate platform balance from wallet ledger.

    Args:
        db: Database session
        currency: Currency code to check

    Returns:
        Dict with 'available' and 'pending' amounts in minor units
    """
    from sqlalchemy import case, func

    # Calculate total deposits (available)
    available_result = (
        db.query(
            func.sum(
                case(
                    (
                        WalletLedger.kind.in_(["deposit", "refund", "dispute_release"]),
                        WalletLedger.delta_minor,
                    ),
                    else_=0,
                )
            )
        )
        .filter(
            WalletLedger.currency == currency,
            WalletLedger.livemode.is_(False),  # Only test mode for now
        )
        .scalar()
    )

    # Calculate total withdrawals (pending)
    pending_result = (
        db.query(
            func.sum(
                case(
                    (
                        WalletLedger.kind.in_(["withdraw", "fee", "dispute_hold"]),
                        func.abs(WalletLedger.delta_minor),
                    ),
                    else_=0,
                )
            )
        )
        .filter(
            WalletLedger.currency == currency,
            WalletLedger.livemode.is_(False),
        )
        .scalar()
    )

    available = available_result if available_result else 0
    pending = pending_result if pending_result else 0

    # Net available = deposits - withdrawals
    net_available = available - pending

    return {"available_minor": net_available, "pending_minor": pending}


def reconcile_stripe_balance(
    db: Session, currency: str = "usd", as_of_date: Optional[date] = None
) -> Dict:
    """
    Reconcile Stripe balance with platform ledger.

    Args:
        db: Database session
        currency: Currency code to reconcile
        as_of_date: Date for reconciliation (default: today)

    Returns:
        Dict with reconciliation results and discrepancies
    """
    if as_of_date is None:
        as_of_date = date.today()

    logger.info(f"Starting reconciliation for {currency} as of {as_of_date}")

    try:
        # Get Stripe balance
        stripe_balance = get_stripe_balance(currency)

        # Calculate platform balance
        platform_balance = calculate_platform_balance(db, currency)

        # Calculate discrepancies
        available_diff = (
            stripe_balance["available_minor"] - platform_balance["available_minor"]
        )
        pending_diff = (
            stripe_balance["pending_minor"] - platform_balance["pending_minor"]
        )

        # Use insert with on conflict to update if exists
        from sqlalchemy.dialects.postgresql import insert

        stmt = insert(StripeReconciliationSnapshot).values(
            as_of_date=as_of_date,
            currency=currency,
            platform_available_minor=platform_balance["available_minor"],
            platform_pending_minor=platform_balance["pending_minor"],
            created_at=datetime.utcnow(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["as_of_date", "currency"],
            set_=dict(
                platform_available_minor=stmt.excluded.platform_available_minor,
                platform_pending_minor=stmt.excluded.platform_pending_minor,
                created_at=stmt.excluded.created_at,
            ),
        )
        db.execute(stmt)
        db.commit()

        result = {
            "as_of_date": as_of_date.isoformat(),
            "currency": currency,
            "stripe_available_minor": stripe_balance["available_minor"],
            "stripe_pending_minor": stripe_balance["pending_minor"],
            "platform_available_minor": platform_balance["available_minor"],
            "platform_pending_minor": platform_balance["pending_minor"],
            "available_discrepancy_minor": available_diff,
            "pending_discrepancy_minor": pending_diff,
            "has_discrepancy": abs(available_diff) > 0 or abs(pending_diff) > 0,
        }

        if result["has_discrepancy"]:
            logger.warning(
                f"Reconciliation discrepancy detected for {currency}: "
                f"available_diff={available_diff}, pending_diff={pending_diff}"
            )
        else:
            logger.info(f"Reconciliation passed for {currency}")

        return result

    except Exception as e:
        logger.error(f"Error during reconciliation: {str(e)}", exc_info=True)
        db.rollback()
        raise


def reconcile_all_currencies(
    db: Session, currencies: List[str] = ["usd"]
) -> List[Dict]:
    """
    Reconcile multiple currencies.

    Args:
        db: Database session
        currencies: List of currency codes to reconcile

    Returns:
        List of reconciliation results
    """
    results = []
    for currency in currencies:
        try:
            result = reconcile_stripe_balance(db, currency)
            results.append(result)
        except Exception as e:
            logger.error(f"Failed to reconcile {currency}: {str(e)}")
            results.append({"currency": currency, "error": str(e)})

    return results
