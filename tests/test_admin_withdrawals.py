"""
Test Admin Withdrawal Endpoints
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient
from fastapi import HTTPException
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, date
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app
from app.models.user import User
from app.models.wallet import WithdrawalRequest
from app.dependencies import get_admin_user
from app.db import get_async_db
from app.services.wallet_service import adjust_wallet_balance
from app.services.stripe_service import create_payout, PayoutFailed

# Test data
STRIPE_TEST_ACCOUNT_ID = "acct_1032D82eZvKYlo2C"
STRIPE_TEST_PAYOUT_ID = "po_1234567890abcdef"


@pytest.fixture
def mock_admin_user():
    """Mock admin user"""
    user = MagicMock(spec=User)
    user.account_id = 99999
    user.email = "admin@triviapay.com"
    user.username = "admin"
    user.is_admin = True
    return user


@pytest.fixture
def mock_withdrawal_request():
    """Mock withdrawal request"""
    withdrawal = MagicMock(spec=WithdrawalRequest)
    withdrawal.id = 1
    withdrawal.user_id = 12345
    withdrawal.amount_minor = 10000  # $100.00
    withdrawal.currency = "usd"
    withdrawal.type = "standard"
    withdrawal.status = "pending_review"
    withdrawal.fee_minor = 250  # $2.50
    withdrawal.stripe_payout_id = None
    withdrawal.requested_at = datetime.utcnow()
    withdrawal.processed_at = None
    withdrawal.admin_notes = None
    withdrawal.livemode = False
    
    # Mock user relationship
    withdrawal.user = MagicMock()
    withdrawal.user.account_id = 12345
    withdrawal.user.username = "testuser"
    withdrawal.user.email = "test@example.com"
    
    return withdrawal


class TestListWithdrawals:
    """Test GET /api/v1/admin/withdrawals"""
    
    @pytest.mark.asyncio
    async def test_list_withdrawals_default(
        self,
        mock_admin_user,
        mock_withdrawal_request
    ):
        """Test listing withdrawals with default filter (pending_review)"""
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_withdrawal_request]
        mock_db_session.execute = AsyncMock(return_value=mock_result)
        
        app.dependency_overrides[get_admin_user] = lambda: mock_admin_user
        app.dependency_overrides[get_async_db] = lambda: mock_db_session
        
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                "/api/v1/admin/withdrawals",
                headers={"Authorization": "Bearer admin_token"}
            )
        
        app.dependency_overrides.clear()
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == 1
        assert data[0]["status"] == "pending_review"
    
    @pytest.mark.asyncio
    async def test_list_withdrawals_with_filters(
        self,
        mock_admin_user,
        mock_withdrawal_request
    ):
        """Test listing withdrawals with status and type filters"""
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_withdrawal_request]
        mock_db_session.execute = AsyncMock(return_value=mock_result)
        
        app.dependency_overrides[get_admin_user] = lambda: mock_admin_user
        app.dependency_overrides[get_async_db] = lambda: mock_db_session
        
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                "/api/v1/admin/withdrawals",
                params={
                    "status_filter": "pending_review",
                    "withdrawal_type": "standard",
                    "limit": 10,
                    "offset": 0
                },
                headers={"Authorization": "Bearer admin_token"}
            )
        
        app.dependency_overrides.clear()
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
    
    @pytest.mark.asyncio
    async def test_list_withdrawals_non_admin(
        self
    ):
        """Test listing withdrawals fails for non-admin"""
        app.dependency_overrides[get_admin_user] = lambda: (_ for _ in ()).throw(HTTPException(status_code=403, detail="Admin access required"))
        
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                "/api/v1/admin/withdrawals",
                headers={"Authorization": "Bearer user_token"}
            )
        
        app.dependency_overrides.clear()
        
        assert response.status_code == 403


class TestApproveWithdrawal:
    """Test POST /api/v1/admin/withdrawals/{id}/approve"""
    
    @pytest.mark.asyncio
    async def test_approve_withdrawal_success(
        self,
        mock_admin_user,
        mock_withdrawal_request
    ):
        """Test successful withdrawal approval"""
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_withdrawal_request
        mock_db_session.execute = AsyncMock(return_value=mock_result)
        
        app.dependency_overrides[get_admin_user] = lambda: mock_admin_user
        app.dependency_overrides[get_async_db] = lambda: mock_db_session
        
        with patch('app.routers.admin_withdrawals.create_payout', return_value={
            'payout_id': STRIPE_TEST_PAYOUT_ID,
            'amount': 10000,
            'currency': 'usd',
            'status': 'paid'
        }), \
        patch('app.routers.admin_withdrawals.adjust_wallet_balance', return_value=10000):
            async with AsyncClient(app=app, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/admin/withdrawals/1/approve",
                    headers={"Authorization": "Bearer admin_token"}
                )
        
        app.dependency_overrides.clear()
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["status"] == "paid"
        assert data["stripe_payout_id"] == STRIPE_TEST_PAYOUT_ID
    
    @pytest.mark.asyncio
    async def test_approve_withdrawal_not_found(
        self,
        mock_admin_user
    ):
        """Test approving non-existent withdrawal"""
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute = AsyncMock(return_value=mock_result)
        
        app.dependency_overrides[get_admin_user] = lambda: mock_admin_user
        app.dependency_overrides[get_async_db] = lambda: mock_db_session
        
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/admin/withdrawals/999/approve",
                headers={"Authorization": "Bearer admin_token"}
            )
        
        app.dependency_overrides.clear()
        
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]
    
    @pytest.mark.asyncio
    async def test_approve_withdrawal_payout_failure(
        self,
        mock_admin_user,
        mock_withdrawal_request
    ):
        """Test withdrawal approval fails when Stripe payout fails"""
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_withdrawal_request
        mock_db_session.execute = AsyncMock(return_value=mock_result)
        
        app.dependency_overrides[get_admin_user] = lambda: mock_admin_user
        app.dependency_overrides[get_async_db] = lambda: mock_db_session
        
        with patch('app.routers.admin_withdrawals.create_payout', side_effect=PayoutFailed("Insufficient funds in Stripe account")):
            async with AsyncClient(app=app, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/admin/withdrawals/1/approve",
                    headers={"Authorization": "Bearer admin_token"}
                )
        
        app.dependency_overrides.clear()
        
        assert response.status_code == 500
        assert "Payout failed" in response.json()["detail"]


class TestRejectWithdrawal:
    """Test POST /api/v1/admin/withdrawals/{id}/reject"""
    
    @pytest.mark.asyncio
    async def test_reject_withdrawal_success(
        self,
        mock_admin_user,
        mock_withdrawal_request
    ):
        """Test successful withdrawal rejection with refund"""
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_withdrawal_request
        mock_db_session.execute = AsyncMock(return_value=mock_result)
        
        app.dependency_overrides[get_admin_user] = lambda: mock_admin_user
        app.dependency_overrides[get_async_db] = lambda: mock_db_session
        
        mock_adjust_balance = MagicMock(return_value=10000)
        with patch('app.routers.admin_withdrawals.adjust_wallet_balance', mock_adjust_balance):
            async with AsyncClient(app=app, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/admin/withdrawals/1/reject",
                    json={"reason": "Suspicious activity detected"},
                    headers={"Authorization": "Bearer admin_token"}
                )
        
        app.dependency_overrides.clear()
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["status"] == "rejected"
        # Verify refund was called
        mock_adjust_balance.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_reject_withdrawal_not_found(
        self,
        mock_admin_user
    ):
        """Test rejecting non-existent withdrawal"""
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute = AsyncMock(return_value=mock_result)
        
        app.dependency_overrides[get_admin_user] = lambda: mock_admin_user
        app.dependency_overrides[get_async_db] = lambda: mock_db_session
        
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/admin/withdrawals/999/reject",
                json={"reason": "Test rejection"},
                headers={"Authorization": "Bearer admin_token"}
            )
        
        app.dependency_overrides.clear()
        
        assert response.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
