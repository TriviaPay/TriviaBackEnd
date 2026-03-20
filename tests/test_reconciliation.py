"""Tests for wallet reconciliation service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.reconciliation_service import run_wallet_reconciliation


def _make_user_row(account_id, balance):
    row = MagicMock()
    row.account_id = account_id
    row.wallet_balance_minor = balance
    return row


def _make_ledger_row(user_id, expected):
    row = MagicMock()
    row.user_id = user_id
    row.expected = expected
    return row


@pytest.mark.asyncio
async def test_all_balances_match():
    db = AsyncMock()

    users = [_make_user_row(1, 500), _make_user_row(2, 1000)]
    ledger = [_make_ledger_row(1, 500), _make_ledger_row(2, 1000)]

    user_result = MagicMock()
    user_result.all.return_value = users

    ledger_result = MagicMock()
    ledger_result.all.return_value = ledger

    empty_result = MagicMock()
    empty_result.all.return_value = []

    # First call returns users, second returns ledger, third returns empty (end batch)
    db.execute.side_effect = [user_result, ledger_result, empty_result]

    summary = await run_wallet_reconciliation(db)

    assert summary["checked"] == 2
    assert summary["matched"] == 2
    assert summary["mismatches"] == []


@pytest.mark.asyncio
async def test_detects_mismatch():
    db = AsyncMock()

    users = [_make_user_row(1, 500), _make_user_row(2, 800)]
    ledger = [_make_ledger_row(1, 500), _make_ledger_row(2, 1000)]

    user_result = MagicMock()
    user_result.all.return_value = users

    ledger_result = MagicMock()
    ledger_result.all.return_value = ledger

    empty_result = MagicMock()
    empty_result.all.return_value = []

    db.execute.side_effect = [user_result, ledger_result, empty_result]

    summary = await run_wallet_reconciliation(db)

    assert summary["checked"] == 2
    assert summary["matched"] == 1
    assert len(summary["mismatches"]) == 1
    assert summary["mismatches"][0]["user_id"] == 2
    assert summary["mismatches"][0]["expected"] == 1000
    assert summary["mismatches"][0]["actual"] == 800
    assert summary["mismatches"][0]["diff"] == -200


@pytest.mark.asyncio
async def test_user_with_no_transactions():
    db = AsyncMock()

    users = [_make_user_row(1, 0)]
    ledger = []  # No transactions for this user

    user_result = MagicMock()
    user_result.all.return_value = users

    ledger_result = MagicMock()
    ledger_result.all.return_value = ledger

    empty_result = MagicMock()
    empty_result.all.return_value = []

    db.execute.side_effect = [user_result, ledger_result, empty_result]

    summary = await run_wallet_reconciliation(db)

    assert summary["checked"] == 1
    assert summary["matched"] == 1  # 0 == 0
    assert summary["mismatches"] == []
