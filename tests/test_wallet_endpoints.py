"""
Test Wallet Endpoints with Stripe Test Data
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, date
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app
from app.models.user import User
from app.models.wallet import WalletTransaction, WithdrawalRequest
from app.services.wallet_service import adjust_wallet_balance
from app.services.stripe_service import create_payout, PayoutFailed

# Stripe Test Account IDs (Express accounts for testing)
STRIPE_TEST_ACCOUNT_ID = "acct_1032D82eZvKYlo2C"  # Standard test account
STRIPE_TEST_ACCOUNT_ID_2 = "acct_1H2K3L4M5N6O7P8Q"  # Another test account

# Stripe Test Payout IDs
STRIPE_TEST_PAYOUT_ID = "po_1234567890abcdef"
STRIPE_TEST_PAYOUT_ID_2 = "po_abcdef1234567890"

# Test user data
TEST_USER_EMAIL = "wallet_test_user@example.com"
TEST_USER_USERNAME = "wallet_test_user"


@pytest.fixture
def mock_get_current_user():
    """Mock authenticated user"""
    user = MagicMock(spec=User)
    user.account_id = 12345
    user.email = TEST_USER_EMAIL
    user.username = TEST_USER_USERNAME
    user.wallet_balance_minor = 100000  # $1000.00
    user.wallet_currency = "usd"
    user.stripe_connect_account_id = STRIPE_TEST_ACCOUNT_ID
    user.instant_withdrawal_enabled = True
    user.instant_withdrawal_daily_limit_minor = 50000  # $500.00 daily limit
    return user


@pytest.fixture
def mock_get_current_user_no_stripe():
    """Mock user without Stripe Connect account"""
    user = MagicMock(spec=User)
    user.account_id = 12346
    user.email = "no_stripe_user@example.com"
    user.username = "no_stripe_user"
    user.wallet_balance_minor = 50000  # $500.00
    user.wallet_currency = "usd"
    user.stripe_connect_account_id = None
    user.instant_withdrawal_enabled = True
    user.instant_withdrawal_daily_limit_minor = 50000
    return user


class TestWalletBalance:
    """Test GET /api/v1/wallet/me endpoint"""
    
    @pytest.mark.asyncio
    async def test_get_wallet_balance_success(
        self, 
        mock_get_current_user
    ):
        """Test successful wallet balance retrieval"""
        from app.dependencies import get_current_user
        from app.db import get_async_db
        from app.routers.wallet import get_wallet_balance
        
        # Override FastAPI dependencies
        mock_db_session = AsyncMock()
        app.dependency_overrides[get_current_user] = lambda: mock_get_current_user
        app.dependency_overrides[get_async_db] = lambda: mock_db_session
        
        # Mock wallet balance function
        with patch('app.routers.wallet.get_wallet_balance', return_value=100000):
            async with AsyncClient(app=app, base_url="http://test") as ac:
                response = await ac.get(
                    "/api/v1/wallet/me",
                    headers={"Authorization": "Bearer test_token"}
                )
        
        # Clean up
        app.dependency_overrides.clear()
        
        assert response.status_code == 200
        data = response.json()
        assert data["balance_minor"] == 100000
        assert data["balance_usd"] == 1000.0
        assert data["currency"] == "usd"
        assert data["stripe_onboarded"] is True
    
    @pytest.mark.asyncio
    @patch('app.routers.wallet.get_current_user')
    @patch('app.routers.wallet.get_async_db')
    @patch('app.routers.wallet.get_wallet_balance')
    async def test_get_wallet_balance_with_transactions(
        self,
        mock_get_balance,
        mock_db,
        mock_get_user,
        mock_get_current_user
    ):
        """Test wallet balance with transaction history"""
        mock_get_user.return_value = mock_get_current_user
        mock_get_balance.return_value = 100000
        
        # Mock transactions
        mock_transaction = MagicMock()
        mock_transaction.id = 1
        mock_transaction.amount_minor = 5000
        mock_transaction.currency = "usd"
        mock_transaction.kind = "deposit"
        mock_transaction.created_at = datetime.utcnow()
        
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_transaction]
        mock_db_session.execute.return_value = mock_result
        mock_db.return_value = mock_db_session
        
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                "/api/v1/wallet/me?include_transactions=true",
                headers={"Authorization": "Bearer test_token"}
            )
        
        assert response.status_code == 200
        data = response.json()
        assert "recent_transactions" in data
        assert len(data["recent_transactions"]) == 1


class TestWithdrawal:
    """Test POST /api/v1/wallet/withdraw endpoint"""
    
    @pytest.mark.asyncio
    @patch('app.routers.wallet.get_current_user')
    @patch('app.routers.wallet.get_async_db')
    @patch('app.routers.wallet.adjust_wallet_balance')
    @patch('app.routers.wallet.get_wallet_balance')
    @patch('app.routers.wallet.calculate_withdrawal_fee')
    @patch('app.routers.wallet.get_daily_instant_withdrawal_count')
    @patch('app.routers.wallet.create_payout')
    async def test_instant_withdrawal_success(
        self,
        mock_create_payout,
        mock_get_daily_count,
        mock_calc_fee,
        mock_get_balance,
        mock_adjust_balance,
        mock_db,
        mock_get_user,
        mock_get_current_user
    ):
        """Test successful instant withdrawal"""
        mock_get_user.return_value = mock_get_current_user
        mock_get_balance.return_value = 100000  # $1000.00
        mock_calc_fee.return_value = 250  # $2.50 fee (0.25% + $0.25)
        mock_get_daily_count.return_value = 0  # No withdrawals today
        mock_adjust_balance.return_value = 97500  # New balance after withdrawal
        
        # Mock successful Stripe payout
        mock_create_payout.return_value = {
            'payout_id': STRIPE_TEST_PAYOUT_ID,
            'amount': 10000,
            'currency': 'usd',
            'status': 'paid'
        }
        
        mock_db_session = AsyncMock()
        mock_db.return_value = mock_db_session
        
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/wallet/withdraw",
                json={
                    "amount_minor": 10000,  # $100.00
                    "type": "instant"
                },
                headers={"Authorization": "Bearer test_token"}
            )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["amount_minor"] == 10000
        assert data["fee_minor"] == 250
        assert data["status"] == "paid"
        assert data["type"] == "instant"
    
    @pytest.mark.asyncio
    @patch('app.routers.wallet.get_current_user')
    @patch('app.routers.wallet.get_async_db')
    @patch('app.routers.wallet.get_wallet_balance')
    async def test_withdrawal_no_stripe_account(
        self,
        mock_get_balance,
        mock_db,
        mock_get_user,
        mock_get_current_user_no_stripe
    ):
        """Test withdrawal fails without Stripe Connect account"""
        mock_get_user.return_value = mock_get_current_user_no_stripe
        mock_get_balance.return_value = 50000
        
        mock_db_session = AsyncMock()
        mock_db.return_value = mock_db_session
        
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/wallet/withdraw",
                json={
                    "amount_minor": 10000,
                    "type": "instant"
                },
                headers={"Authorization": "Bearer test_token"}
            )
        
        assert response.status_code == 400
        assert "Stripe Connect account not set up" in response.json()["detail"]
    
    @pytest.mark.asyncio
    @patch('app.routers.wallet.get_current_user')
    @patch('app.routers.wallet.get_async_db')
    @patch('app.routers.wallet.get_wallet_balance')
    @patch('app.routers.wallet.calculate_withdrawal_fee')
    async def test_withdrawal_insufficient_balance(
        self,
        mock_calc_fee,
        mock_get_balance,
        mock_db,
        mock_get_user,
        mock_get_current_user
    ):
        """Test withdrawal fails with insufficient balance"""
        mock_get_user.return_value = mock_get_current_user
        mock_get_balance.return_value = 5000  # Only $50.00
        mock_calc_fee.return_value = 250
        
        mock_db_session = AsyncMock()
        mock_db.return_value = mock_db_session
        
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/wallet/withdraw",
                json={
                    "amount_minor": 10000,  # Trying to withdraw $100.00
                    "type": "instant"
                },
                headers={"Authorization": "Bearer test_token"}
            )
        
        assert response.status_code == 400
        assert "Insufficient balance" in response.json()["detail"]
    
    @pytest.mark.asyncio
    @patch('app.routers.wallet.get_current_user')
    @patch('app.routers.wallet.get_async_db')
    @patch('app.routers.wallet.get_wallet_balance')
    @patch('app.routers.wallet.calculate_withdrawal_fee')
    @patch('app.routers.wallet.get_daily_instant_withdrawal_count')
    async def test_instant_withdrawal_daily_limit_exceeded(
        self,
        mock_get_daily_count,
        mock_calc_fee,
        mock_get_balance,
        mock_db,
        mock_get_user,
        mock_get_current_user
    ):
        """Test instant withdrawal fails when daily limit exceeded"""
        mock_get_user.return_value = mock_get_current_user
        mock_get_balance.return_value = 100000
        mock_calc_fee.return_value = 250
        mock_get_daily_count.return_value = 45000  # Already withdrew $450 today
        
        mock_db_session = AsyncMock()
        mock_db.return_value = mock_db_session
        
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/wallet/withdraw",
                json={
                    "amount_minor": 10000,  # Would exceed $500 daily limit
                    "type": "instant"
                },
                headers={"Authorization": "Bearer test_token"}
            )
        
        assert response.status_code == 400
        assert "Daily instant withdrawal limit exceeded" in response.json()["detail"]
    
    @pytest.mark.asyncio
    @patch('app.routers.wallet.get_current_user')
    @patch('app.routers.wallet.get_async_db')
    @patch('app.routers.wallet.adjust_wallet_balance')
    @patch('app.routers.wallet.get_wallet_balance')
    @patch('app.routers.wallet.calculate_withdrawal_fee')
    @patch('app.routers.wallet.get_daily_instant_withdrawal_count')
    @patch('app.routers.wallet.create_payout')
    async def test_instant_withdrawal_payout_failure_refund(
        self,
        mock_create_payout,
        mock_get_daily_count,
        mock_calc_fee,
        mock_get_balance,
        mock_adjust_balance,
        mock_db,
        mock_get_user,
        mock_get_current_user
    ):
        """Test instant withdrawal refunds when Stripe payout fails"""
        mock_get_user.return_value = mock_get_current_user
        mock_get_balance.return_value = 100000
        mock_calc_fee.return_value = 250
        mock_get_daily_count.return_value = 0
        mock_adjust_balance.return_value = 97500
        
        # Mock Stripe payout failure
        mock_create_payout.side_effect = PayoutFailed("Insufficient funds in Stripe account")
        
        mock_db_session = AsyncMock()
        mock_db.return_value = mock_db_session
        
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/wallet/withdraw",
                json={
                    "amount_minor": 10000,
                    "type": "instant"
                },
                headers={"Authorization": "Bearer test_token"}
            )
        
        assert response.status_code == 500
        assert "refunded to your wallet" in response.json()["detail"]
        # Verify refund was called
        assert mock_adjust_balance.call_count >= 2  # Once for debit, once for refund
    
    @pytest.mark.asyncio
    @patch('app.routers.wallet.get_current_user')
    @patch('app.routers.wallet.get_async_db')
    @patch('app.routers.wallet.adjust_wallet_balance')
    @patch('app.routers.wallet.get_wallet_balance')
    @patch('app.routers.wallet.calculate_withdrawal_fee')
    async def test_standard_withdrawal_creates_pending(
        self,
        mock_calc_fee,
        mock_get_balance,
        mock_adjust_balance,
        mock_db,
        mock_get_user,
        mock_get_current_user
    ):
        """Test standard withdrawal creates pending_review request"""
        mock_get_user.return_value = mock_get_current_user
        mock_get_balance.return_value = 100000
        mock_calc_fee.return_value = 250
        mock_adjust_balance.return_value = 97500
        
        mock_db_session = AsyncMock()
        mock_db.return_value = mock_db_session
        
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/wallet/withdraw",
                json={
                    "amount_minor": 10000,
                    "type": "standard"
                },
                headers={"Authorization": "Bearer test_token"}
            )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending_review"
        assert data["type"] == "standard"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

