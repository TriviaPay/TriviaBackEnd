"""
Test IAP (In-App Purchase) Endpoints with Mock Receipt Data
"""

import base64
import json
import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import get_async_db
from app.dependencies import get_current_user
from app.models.user import User
from app.models.wallet import IapReceipt
from app.services.iap_service import (
    get_product_credit_amount,
    verify_apple_receipt,
    verify_google_purchase,
)
from app.services.wallet_service import adjust_wallet_balance
from main import app

# Test Product IDs
TEST_PRODUCT_AVATAR = "AV001"
TEST_PRODUCT_FRAME = "FR001"
TEST_PRODUCT_GEMS = "GP001"
TEST_PRODUCT_BADGE = "BD001"

# Test Transaction IDs
APPLE_TEST_TRANSACTION_ID = "1000000123456789"
APPLE_TEST_TRANSACTION_ID_2 = "1000000987654321"
GOOGLE_TEST_TRANSACTION_ID = "GPA.1234-5678-9012-34567"
GOOGLE_TEST_TRANSACTION_ID_2 = "GPA.9876-5432-1098-76543"

# Mock Receipt Data (Base64 encoded)
MOCK_APPLE_RECEIPT = base64.b64encode(
    json.dumps(
        {
            "receipt_type": "ProductionSandbox",
            "bundle_id": "com.triviapay.app",
            "receipt_creation_date_ms": "1704067200000",
            "in_app": [
                {
                    "transaction_id": APPLE_TEST_TRANSACTION_ID,
                    "product_id": TEST_PRODUCT_GEMS,
                    "purchase_date_ms": "1704067200000",
                }
            ],
        }
    ).encode()
).decode()

MOCK_GOOGLE_PURCHASE_TOKEN = "opaque-token-up_to_150_characters_abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@pytest.fixture
def mock_user():
    """Mock authenticated user"""
    user = MagicMock(spec=User)
    user.account_id = 12345
    user.email = "iap_test@example.com"
    user.username = "iap_test_user"
    user.wallet_balance_minor = 0
    user.wallet_currency = "usd"
    return user


class TestAppleIAPVerification:
    """Test POST /api/v1/iap/apple/verify"""

    @pytest.mark.asyncio
    async def test_apple_verify_success(self, mock_user):
        """Test successful Apple receipt verification"""
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_current_user] = lambda: mock_user
        app.dependency_overrides[get_async_db] = lambda: mock_db_session

        with patch(
            "app.routers.payments.iap.verify_apple_receipt",
            return_value={
                "verified": True,
                "transaction_id": APPLE_TEST_TRANSACTION_ID,
                "product_id": TEST_PRODUCT_GEMS,
                "purchase_date": "2024-01-01T00:00:00Z",
                "environment": "production",
            },
        ), patch(
            "app.routers.payments.iap.get_product_credit_amount", return_value=49900
        ), patch(
            "app.routers.payments.iap.adjust_wallet_balance", return_value=49900
        ):
            async with AsyncClient(app=app, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/iap/apple/verify",
                    json={
                        "receipt_data": MOCK_APPLE_RECEIPT,
                        "product_id": TEST_PRODUCT_GEMS,
                        "environment": "production",
                    },
                    headers={"Authorization": "Bearer test_token"},
                )

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["transaction_id"] == APPLE_TEST_TRANSACTION_ID
        assert data["product_id"] == TEST_PRODUCT_GEMS
        assert data["credited_amount_minor"] == 49900
        assert data["credited_amount_usd"] == 499.0

    @pytest.mark.asyncio
    async def test_apple_verify_failed_verification(self, mock_user):
        """Test Apple verification fails when receipt is invalid"""
        mock_db_session = AsyncMock()
        app.dependency_overrides[get_current_user] = lambda: mock_user
        app.dependency_overrides[get_async_db] = lambda: mock_db_session

        with patch(
            "app.routers.payments.iap.verify_apple_receipt",
            return_value={"verified": False, "error": "Invalid receipt signature"},
        ):
            async with AsyncClient(app=app, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/iap/apple/verify",
                    json={
                        "receipt_data": "invalid_receipt_data",
                        "product_id": TEST_PRODUCT_GEMS,
                        "environment": "production",
                    },
                    headers={"Authorization": "Bearer test_token"},
                )

        app.dependency_overrides.clear()

        assert response.status_code == 400
        assert "verification failed" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_apple_verify_product_not_found(self, mock_user):
        """Test Apple verification fails when product not found"""
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_current_user] = lambda: mock_user
        app.dependency_overrides[get_async_db] = lambda: mock_db_session

        with patch(
            "app.routers.payments.iap.verify_apple_receipt",
            return_value={
                "verified": True,
                "transaction_id": APPLE_TEST_TRANSACTION_ID,
                "product_id": "INVALID_PRODUCT",
                "purchase_date": "2024-01-01T00:00:00Z",
                "environment": "production",
            },
        ), patch(
            "app.routers.payments.iap.get_product_credit_amount", return_value=None
        ):
            async with AsyncClient(app=app, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/iap/apple/verify",
                    json={
                        "receipt_data": MOCK_APPLE_RECEIPT,
                        "product_id": "INVALID_PRODUCT",
                        "environment": "production",
                    },
                    headers={"Authorization": "Bearer test_token"},
                )

        app.dependency_overrides.clear()

        assert response.status_code == 400
        assert "not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_apple_verify_idempotency(self, mock_user):
        """Test Apple verification is idempotent (same receipt twice)"""
        existing_receipt = MagicMock()
        existing_receipt.id = 1
        existing_receipt.product_id = TEST_PRODUCT_GEMS
        existing_receipt.credited_amount_minor = 49900
        existing_receipt.transaction_id = APPLE_TEST_TRANSACTION_ID

        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_receipt
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_current_user] = lambda: mock_user
        app.dependency_overrides[get_async_db] = lambda: mock_db_session

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/iap/apple/verify",
                json={
                    "receipt_data": MOCK_APPLE_RECEIPT,
                    "product_id": TEST_PRODUCT_GEMS,
                    "environment": "production",
                },
                headers={"Authorization": "Bearer test_token"},
            )

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["receipt_id"] == 1  # Returns existing receipt


class TestGoogleIAPVerification:
    """Test POST /api/v1/iap/google/verify"""

    @pytest.mark.asyncio
    async def test_google_verify_success(self, mock_user):
        """Test successful Google purchase verification"""
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_current_user] = lambda: mock_user
        app.dependency_overrides[get_async_db] = lambda: mock_db_session

        with patch(
            "app.routers.payments.iap.verify_google_purchase",
            return_value={
                "verified": True,
                "transaction_id": GOOGLE_TEST_TRANSACTION_ID,
                "product_id": TEST_PRODUCT_GEMS,
                "purchase_time": 1704067200000,
                "purchase_state": 0,
            },
        ), patch(
            "app.routers.payments.iap.get_product_credit_amount", return_value=49900
        ), patch(
            "app.routers.payments.iap.adjust_wallet_balance", return_value=49900
        ):
            async with AsyncClient(app=app, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/iap/google/verify",
                    json={
                        "package_name": "com.triviapay.app",
                        "product_id": TEST_PRODUCT_GEMS,
                        "purchase_token": MOCK_GOOGLE_PURCHASE_TOKEN,
                    },
                    headers={"Authorization": "Bearer test_token"},
                )

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["transaction_id"] == GOOGLE_TEST_TRANSACTION_ID
        assert data["product_id"] == TEST_PRODUCT_GEMS
        assert data["credited_amount_minor"] == 49900

    @pytest.mark.asyncio
    async def test_google_verify_failed_verification(self, mock_user):
        """Test Google verification fails when purchase is invalid"""
        mock_db_session = AsyncMock()
        app.dependency_overrides[get_current_user] = lambda: mock_user
        app.dependency_overrides[get_async_db] = lambda: mock_db_session

        with patch(
            "app.routers.payments.iap.verify_google_purchase",
            return_value={"verified": False, "error": "Invalid purchase token"},
        ):
            async with AsyncClient(app=app, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/iap/google/verify",
                    json={
                        "package_name": "com.triviapay.app",
                        "product_id": TEST_PRODUCT_GEMS,
                        "purchase_token": "invalid_token",
                    },
                    headers={"Authorization": "Bearer test_token"},
                )

        app.dependency_overrides.clear()

        assert response.status_code == 400
        assert "verification failed" in response.json()["detail"]


class TestIAPWebhooks:
    """Test IAP webhook endpoints"""

    @pytest.mark.asyncio
    async def test_apple_webhook_not_implemented(self):
        """Test Apple webhook endpoint (placeholder)"""
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post("/api/v1/iap/apple/webhook")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "not_implemented"

    @pytest.mark.asyncio
    async def test_google_webhook_not_implemented(self):
        """Test Google webhook endpoint (placeholder)"""
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post("/api/v1/iap/google/webhook")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "not_implemented"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
