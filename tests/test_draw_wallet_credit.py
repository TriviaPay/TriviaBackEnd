"""Tests for draw reward wallet crediting via background queue."""

import asyncio
from datetime import date
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from workers.handlers import handle_task


class TestWalletCreditWinnerHandler:
    """Tests for the wallet.credit_winner task handler."""

    def test_handler_calls_adjust_wallet_balance(self):
        payload = {
            "account_id": 123,
            "amount_minor": 250,
            "reason": "bronze_draw_2026-03-13",
            "idempotency_key": "draw_reward:bronze:2026-03-13:123",
        }

        with patch("workers.handlers.AsyncSessionLocal") as mock_session_cls, \
             patch("workers.handlers.adjust_wallet_balance") as mock_adjust:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session.begin = MagicMock(return_value=MagicMock(
                __aenter__=AsyncMock(return_value=None),
                __aexit__=AsyncMock(return_value=None),
            ))
            mock_session_cls.return_value = mock_session
            mock_adjust.return_value = 250

            handle_task("wallet.credit_winner", payload)

            mock_adjust.assert_called_once_with(
                db=mock_session,
                user_id=123,
                currency="usd",
                delta_minor=250,
                kind="deposit",
                external_ref_type="draw_reward",
                external_ref_id="bronze_draw_2026-03-13",
                event_id="draw_reward:bronze:2026-03-13:123",
                livemode=True,
            )


class TestBronzeModeEnqueues:
    """Tests that bronze mode distribute_rewards enqueues wallet credits."""

    def test_enqueues_wallet_credit_for_each_winner(self):
        with patch("utils.bronze_mode_service.enqueue_task") as mock_enqueue, \
             patch("utils.bronze_mode_service.calculate_harmonic_sum_rewards") as mock_rewards:
            mock_rewards.return_value = [1.50, 0.75]

            # We need to mock the DB operations
            mock_db = MagicMock()
            mock_db.query.return_value.filter.return_value.first.return_value = None

            winners = [
                {"account_id": 1, "position": 1, "submitted_at": "2026-03-13T10:00:00"},
                {"account_id": 2, "position": 2, "submitted_at": "2026-03-13T10:01:00"},
            ]

            from utils.bronze_mode_service import distribute_rewards_to_winners_bronze_mode
            distribute_rewards_to_winners_bronze_mode(mock_db, winners, date(2026, 3, 13), 2.25)

            assert mock_enqueue.call_count == 2

            first_call = mock_enqueue.call_args_list[0]
            assert first_call.kwargs["name"] == "wallet.credit_winner"
            assert first_call.kwargs["payload"]["account_id"] == 1
            assert first_call.kwargs["payload"]["amount_minor"] == 150
            assert first_call.kwargs["payload"]["idempotency_key"] == "draw_reward:bronze:2026-03-13:1"

            second_call = mock_enqueue.call_args_list[1]
            assert second_call.kwargs["payload"]["account_id"] == 2
            assert second_call.kwargs["payload"]["amount_minor"] == 75

    def test_skips_zero_rewards(self):
        with patch("utils.bronze_mode_service.enqueue_task") as mock_enqueue, \
             patch("utils.bronze_mode_service.calculate_harmonic_sum_rewards") as mock_rewards:
            mock_rewards.return_value = [0.0]

            mock_db = MagicMock()
            mock_db.query.return_value.filter.return_value.first.return_value = None

            winners = [
                {"account_id": 1, "position": 1, "submitted_at": "2026-03-13T10:00:00"},
            ]

            from utils.bronze_mode_service import distribute_rewards_to_winners_bronze_mode
            distribute_rewards_to_winners_bronze_mode(mock_db, winners, date(2026, 3, 13), 0.0)

            mock_enqueue.assert_not_called()


class TestSilverModeEnqueues:
    """Tests that silver mode distribute_rewards enqueues wallet credits."""

    def test_enqueues_wallet_credit_for_winner(self):
        with patch("utils.silver_mode_service.enqueue_task") as mock_enqueue, \
             patch("utils.silver_mode_service.calculate_harmonic_sum_rewards") as mock_rewards:
            mock_rewards.return_value = [3.00]

            mock_db = MagicMock()
            mock_db.query.return_value.filter.return_value.first.return_value = None

            winners = [
                {"account_id": 5, "position": 1, "submitted_at": "2026-03-13T10:00:00"},
            ]

            from utils.silver_mode_service import distribute_rewards_to_winners_silver_mode
            distribute_rewards_to_winners_silver_mode(mock_db, winners, date(2026, 3, 13), 3.00)

            mock_enqueue.assert_called_once()
            call_payload = mock_enqueue.call_args.kwargs["payload"]
            assert call_payload["account_id"] == 5
            assert call_payload["amount_minor"] == 300
            assert "silver" in call_payload["idempotency_key"]
