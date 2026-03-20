"""Tests for subscription activation from IAP purchases."""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.subscription_iap_service import (
    activate_subscription_from_iap,
    lookup_subscription_plan,
    _compute_period_end,
)


class TestComputePeriodEnd:
    def _make_plan(self, interval="month", interval_count=1, billing_interval=None):
        plan = MagicMock()
        plan.interval = interval
        plan.interval_count = interval_count
        plan.billing_interval = billing_interval
        return plan

    def test_monthly(self):
        start = datetime(2026, 3, 1, tzinfo=timezone.utc)
        end = _compute_period_end(start, self._make_plan("month", 1))
        assert end.month == 4
        assert end.day == 1

    def test_yearly(self):
        start = datetime(2026, 3, 1, tzinfo=timezone.utc)
        end = _compute_period_end(start, self._make_plan("year", 1))
        assert end.year == 2027

    def test_weekly(self):
        start = datetime(2026, 3, 1, tzinfo=timezone.utc)
        end = _compute_period_end(start, self._make_plan("week", 2))
        assert end == start + timedelta(weeks=2)

    def test_daily(self):
        start = datetime(2026, 3, 1, tzinfo=timezone.utc)
        end = _compute_period_end(start, self._make_plan("day", 7))
        assert end == start + timedelta(days=7)

    def test_fallback_to_billing_interval(self):
        start = datetime(2026, 3, 1, tzinfo=timezone.utc)
        end = _compute_period_end(start, self._make_plan(None, 1, "year"))
        assert end.year == 2027


@pytest.mark.asyncio
class TestActivateSubscription:
    async def test_creates_new_subscription(self):
        db = AsyncMock()
        # No existing subscription
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result
        db.flush = AsyncMock()

        plan = MagicMock()
        plan.id = 1
        plan.name = "Bronze"
        plan.interval = "month"
        plan.interval_count = 1
        plan.billing_interval = "month"

        result = await activate_subscription_from_iap(
            db, user_id=123, plan=plan, receipt_id=456, livemode=True,
        )

        assert result["plan_name"] == "Bronze"
        assert result["status"] == "active"
        assert result["current_period_end"] is not None
        db.add.assert_called_once()

    async def test_extends_active_subscription(self):
        now = datetime.now(timezone.utc)
        existing = MagicMock()
        existing.id = 10
        existing.status = "active"
        existing.current_period_end = now + timedelta(days=15)
        existing.current_period_start = now - timedelta(days=15)

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        db.execute.return_value = mock_result
        db.flush = AsyncMock()

        plan = MagicMock()
        plan.id = 1
        plan.name = "Bronze"
        plan.interval = "month"
        plan.interval_count = 1
        plan.billing_interval = "month"

        result = await activate_subscription_from_iap(
            db, user_id=123, plan=plan, receipt_id=456, livemode=True,
        )

        assert result["status"] == "active"
        # Period end should be extended from original end, not from now
        assert existing.current_period_end > now + timedelta(days=15)

    async def test_reactivates_canceled_subscription(self):
        now = datetime.now(timezone.utc)
        existing = MagicMock()
        existing.id = 10
        existing.status = "canceled"
        existing.current_period_end = now - timedelta(days=5)

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        db.execute.return_value = mock_result
        db.flush = AsyncMock()

        plan = MagicMock()
        plan.id = 1
        plan.name = "Silver"
        plan.interval = "month"
        plan.interval_count = 1
        plan.billing_interval = "month"

        result = await activate_subscription_from_iap(
            db, user_id=123, plan=plan, receipt_id=789, livemode=True,
        )

        assert existing.status == "active"
        assert existing.cancel_at_period_end is False
        assert existing.canceled_at is None


@pytest.mark.asyncio
class TestLookupSubscriptionPlan:
    async def test_apple_lookup(self):
        db = AsyncMock()
        mock_plan = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_plan
        db.execute.return_value = mock_result

        result = await lookup_subscription_plan(db, platform="apple", product_id="com.triviapay.bronze")
        assert result == mock_plan

    async def test_google_lookup(self):
        db = AsyncMock()
        mock_plan = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_plan
        db.execute.return_value = mock_result

        result = await lookup_subscription_plan(db, platform="google", product_id="bronze_monthly")
        assert result == mock_plan

    async def test_unknown_platform_returns_none(self):
        db = AsyncMock()
        result = await lookup_subscription_plan(db, platform="unknown", product_id="test")
        assert result is None
