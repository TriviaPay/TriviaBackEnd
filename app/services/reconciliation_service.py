"""Wallet reconciliation service.

Compares User.wallet_balance_minor against the sum of all
WalletTransaction records to detect drift. Report-only — does not
auto-correct balances.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.wallet import WalletTransaction

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


async def run_wallet_reconciliation(db: AsyncSession) -> Dict[str, Any]:
    """Run a full wallet reconciliation.

    Compares each user's ``wallet_balance_minor`` against
    ``SUM(wallet_transactions.amount_minor)``.

    Returns:
        {
            "checked": int,
            "matched": int,
            "mismatches": [{"user_id", "expected", "actual", "diff"}],
        }
    """
    # Compute expected balances from the transaction ledger
    ledger_stmt = (
        select(
            WalletTransaction.user_id,
            func.coalesce(func.sum(WalletTransaction.amount_minor), 0).label("expected"),
        )
        .group_by(WalletTransaction.user_id)
    )

    mismatches: List[Dict[str, Any]] = []
    checked = 0
    matched = 0

    # Stream through users in batches
    offset = 0
    while True:
        user_stmt = (
            select(User.account_id, User.wallet_balance_minor)
            .order_by(User.account_id)
            .offset(offset)
            .limit(BATCH_SIZE)
        )
        user_result = await db.execute(user_stmt)
        users = user_result.all()

        if not users:
            break

        user_ids = [u.account_id for u in users]
        user_balances = {u.account_id: (u.wallet_balance_minor or 0) for u in users}

        # Get ledger sums for this batch
        batch_ledger_stmt = (
            select(
                WalletTransaction.user_id,
                func.coalesce(func.sum(WalletTransaction.amount_minor), 0).label("expected"),
            )
            .where(WalletTransaction.user_id.in_(user_ids))
            .group_by(WalletTransaction.user_id)
        )
        ledger_result = await db.execute(batch_ledger_stmt)
        ledger_sums = {row.user_id: int(row.expected) for row in ledger_result.all()}

        for uid in user_ids:
            actual = user_balances[uid]
            expected = ledger_sums.get(uid, 0)
            checked += 1

            if actual != expected:
                diff = actual - expected
                mismatches.append({
                    "user_id": uid,
                    "expected": expected,
                    "actual": actual,
                    "diff": diff,
                })
                logger.warning(
                    "Wallet mismatch: user=%s expected=%s actual=%s diff=%s",
                    uid, expected, actual, diff,
                )
            else:
                matched += 1

        offset += BATCH_SIZE

    summary = {
        "checked": checked,
        "matched": matched,
        "mismatches": mismatches,
    }

    if mismatches:
        logger.error(
            "Wallet reconciliation found %d mismatches out of %d users",
            len(mismatches), checked,
        )
    else:
        logger.info("Wallet reconciliation passed: %d users checked, all matched", checked)

    return summary
