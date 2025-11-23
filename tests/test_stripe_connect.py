"""
Test Stripe Connect Endpoints with Test Account Data
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock, MagicMock
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app
from app.models.user import User
from app.dependencies import get_current_user
from app.db import get_async_db
from app.services.stripe_service import create_or_get_connect_account, create_account_link, StripeError

# Stripe Test Account IDs
STRIPE_TEST_ACCOUNT_ID = "acct_1032D82eZvKYlo2C"  # Standard test Express account
STRIPE_TEST_ACCOUNT_ID_NEW = "acct_1H2K3L4M5N6O7P8Q"  # New test account

# Stripe Test Account Link URLs
STRIPE_TEST_ONBOARDING_URL = "https://connect.stripe.com/setup/c/test_account_link"
STRIPE_TEST_REFRESH_URL = "https://connect.stripe.com/setup/r/test_account_link"

# Stripe Test Publishable Keys
STRIPE_TEST_PUBLISHABLE_KEY = "pk_test_51234567890abcdefghijklmnopqrstuvwxyz1234567890"


@pytest.fixture
def mock_user():
    """Mock user with Stripe account"""
    user = MagicMock(spec=User)
    user.account_id = 12345
    user.email = "test@example.com"
    user.username = "testuser"
    user.stripe_connect_account_id = STRIPE_TEST_ACCOUNT_ID
    return user


@pytest.fixture
def mock_user_no_stripe():
    """Mock user without Stripe account"""
    user = MagicMock(spec=User)
    user.account_id = 12346
    user.email = "test2@example.com"
    user.username = "testuser2"
    user.stripe_connect_account_id = None
    return user


class TestStripeConnectAccountLink:
    """Test POST /api/v1/stripe/connect/create-account-link"""
    
    @pytest.mark.asyncio
    async def test_create_account_link_new_account(
        self,
        mock_user_no_stripe
    ):
        """Test creating account link for new user"""
        from app.routers.stripe_connect import create_or_get_connect_account, create_account_link
        
        mock_db_session = AsyncMock()
        app.dependency_overrides[get_current_user] = lambda: mock_user_no_stripe
        app.dependency_overrides[get_async_db] = lambda: mock_db_session
        
        with patch('app.routers.stripe_connect.create_or_get_connect_account', return_value=STRIPE_TEST_ACCOUNT_ID_NEW), \
             patch('app.routers.stripe_connect.create_account_link', return_value={
                 'url': STRIPE_TEST_ONBOARDING_URL,
                 'expires_at': 1234567890
             }):
            async with AsyncClient(app=app, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/stripe/connect/create-account-link",
                    headers={"Authorization": "Bearer test_token"}
                )
        
        app.dependency_overrides.clear()
        
        assert response.status_code == 200
        data = response.json()
        assert data["url"] == STRIPE_TEST_ONBOARDING_URL
        assert data["account_id"] == STRIPE_TEST_ACCOUNT_ID_NEW
    
    @pytest.mark.asyncio
    async def test_create_account_link_existing_account(
        self,
        mock_user
    ):
        """Test creating account link for existing account"""
        mock_db_session = AsyncMock()
        app.dependency_overrides[get_current_user] = lambda: mock_user
        app.dependency_overrides[get_async_db] = lambda: mock_db_session
        
        with patch('app.routers.stripe_connect.create_or_get_connect_account', return_value=STRIPE_TEST_ACCOUNT_ID), \
             patch('app.routers.stripe_connect.create_account_link', return_value={
                 'url': STRIPE_TEST_REFRESH_URL,
                 'expires_at': 1234567890
             }):
            async with AsyncClient(app=app, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/stripe/connect/create-account-link",
                    headers={"Authorization": "Bearer test_token"}
                )
        
        app.dependency_overrides.clear()
        
        assert response.status_code == 200
        data = response.json()
        assert data["url"] == STRIPE_TEST_REFRESH_URL
        assert data["account_id"] == STRIPE_TEST_ACCOUNT_ID
    
    @pytest.mark.asyncio
    async def test_create_account_link_with_custom_urls(
        self,
        mock_user_no_stripe
    ):
        """Test creating account link with custom return/refresh URLs"""
        mock_db_session = AsyncMock()
        app.dependency_overrides[get_current_user] = lambda: mock_user_no_stripe
        app.dependency_overrides[get_async_db] = lambda: mock_db_session
        
        mock_create_link = MagicMock(return_value={
            'url': STRIPE_TEST_ONBOARDING_URL,
            'expires_at': 1234567890
        })
        
        with patch('app.routers.stripe_connect.create_or_get_connect_account', return_value=STRIPE_TEST_ACCOUNT_ID_NEW), \
             patch('app.routers.stripe_connect.create_account_link', mock_create_link):
            async with AsyncClient(app=app, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/stripe/connect/create-account-link",
                    params={
                        "return_url": "https://app.triviapay.com/onboarding/success",
                        "refresh_url": "https://app.triviapay.com/onboarding/refresh"
                    },
                    headers={"Authorization": "Bearer test_token"}
                )
        
        app.dependency_overrides.clear()
        
        assert response.status_code == 200
        # Verify custom URLs were passed to create_account_link
        mock_create_link.assert_called_once()
        call_args = mock_create_link.call_args
        assert "https://app.triviapay.com/onboarding/success" in str(call_args)
    
    @pytest.mark.asyncio
    async def test_create_account_link_stripe_error(
        self,
        mock_user_no_stripe
    ):
        """Test handling Stripe API errors"""
        mock_db_session = AsyncMock()
        app.dependency_overrides[get_current_user] = lambda: mock_user_no_stripe
        app.dependency_overrides[get_async_db] = lambda: mock_db_session
        
        with patch('app.routers.stripe_connect.create_or_get_connect_account', side_effect=StripeError("Stripe API error: Rate limit exceeded")):
            async with AsyncClient(app=app, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/stripe/connect/create-account-link",
                    headers={"Authorization": "Bearer test_token"}
                )
        
        app.dependency_overrides.clear()
        
        assert response.status_code == 500
        assert "Stripe API error" in response.json()["detail"]


class TestRefreshAccountLink:
    """Test POST /api/v1/stripe/connect/refresh-account-link"""
    
    @pytest.mark.asyncio
    async def test_refresh_account_link_success(
        self,
        mock_user
    ):
        """Test refreshing account link for existing account"""
        app.dependency_overrides[get_current_user] = lambda: mock_user
        
        with patch('app.routers.stripe_connect.create_account_link', return_value={
            'url': STRIPE_TEST_REFRESH_URL,
            'expires_at': 1234567890
        }):
            async with AsyncClient(app=app, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/stripe/connect/refresh-account-link",
                    headers={"Authorization": "Bearer test_token"}
                )
        
        app.dependency_overrides.clear()
        
        assert response.status_code == 200
        data = response.json()
        assert data["url"] == STRIPE_TEST_REFRESH_URL
        assert data["account_id"] == STRIPE_TEST_ACCOUNT_ID
    
    @pytest.mark.asyncio
    async def test_refresh_account_link_no_account(
        self,
        mock_user_no_stripe
    ):
        """Test refreshing account link fails without account"""
        app.dependency_overrides[get_current_user] = lambda: mock_user_no_stripe
        
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/stripe/connect/refresh-account-link",
                headers={"Authorization": "Bearer test_token"}
            )
        
        app.dependency_overrides.clear()
        
        assert response.status_code == 400
        assert "No Stripe Connect account found" in response.json()["detail"]


class TestPublishableKey:
    """Test GET /api/v1/stripe/connect/publishable-key"""
    
    @pytest.mark.asyncio
    async def test_get_publishable_key_success(self):
        """Test getting publishable key"""
        with patch('app.routers.stripe_connect.get_publishable_key', return_value=STRIPE_TEST_PUBLISHABLE_KEY):
            async with AsyncClient(app=app, base_url="http://test") as ac:
                response = await ac.get("/api/v1/stripe/connect/publishable-key")
        
        assert response.status_code == 200
        data = response.json()
        assert data["publishable_key"] == STRIPE_TEST_PUBLISHABLE_KEY
        assert data["publishable_key"].startswith("pk_test_")
    
    @pytest.mark.asyncio
    async def test_get_publishable_key_not_configured(self):
        """Test getting publishable key when not configured"""
        with patch('app.routers.stripe_connect.get_publishable_key', return_value=None):
            async with AsyncClient(app=app, base_url="http://test") as ac:
                response = await ac.get("/api/v1/stripe/connect/publishable-key")
        
        assert response.status_code == 503
        assert "not configured" in response.json()["detail"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
