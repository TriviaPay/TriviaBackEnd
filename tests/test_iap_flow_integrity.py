"""
IAP flow integrity tests: idempotency, concurrency, webhooks, and mocked platform verification.
"""

import asyncio
import base64
import json
from datetime import datetime, timezone

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import core.config as config
from app.db import Base as AsyncBase
from app.models.products import GemPackageConfig
from app.models.user import User
from app.models.wallet import IapReceipt, WalletTransaction
from app.routers.payments import service as payments_service
from app.routers.payments.service import (
    process_apple_notification,
    process_google_notification,
)
from app.services import apple_iap_service, google_iap_service
from app.services.apple_iap_service import process_apple_iap
from app.services.google_iap_service import process_google_iap
from app.services.wallet_service import adjust_wallet_balance


TEST_PRODUCT_GEMS = "GP001"
APPLE_TEST_TRANSACTION_ID = "1000000123456789"
GOOGLE_TEST_TRANSACTION_ID = "GPA.1234-5678-9012-34567"
GOOGLE_TEST_PURCHASE_TOKEN = "opaque-token-abcdefghijklmnopqrstuvwxyz"


@pytest_asyncio.fixture
async def async_engine(tmp_path):
    db_path = tmp_path / "iap_tests.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(AsyncBase.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def async_session_maker(async_engine):
    return async_sessionmaker(async_engine, expire_on_commit=False)


async def seed_user_and_product(session, *, account_id=1, price_minor=499):
    user = User(
        account_id=account_id,
        email=f"iap_user_{account_id}@example.com",
        username=f"iap_user_{account_id}",
        wallet_balance_minor=0,
        wallet_currency="usd",
    )
    session.add(user)
    package = GemPackageConfig(
        id=1,
        product_id=TEST_PRODUCT_GEMS,
        price_minor=price_minor,
        product_type="consumable",
        gems_amount=100,
        is_one_time=False,
    )
    session.add(package)
    await session.commit()


async def get_user(session, account_id=1):
    result = await session.execute(select(User).where(User.account_id == account_id))
    return result.scalar_one()


def build_apple_payload(transaction_id=APPLE_TEST_TRANSACTION_ID):
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    return {
        "transactionId": transaction_id,
        "originalTransactionId": transaction_id,
        "webOrderLineItemId": transaction_id,
        "productId": TEST_PRODUCT_GEMS,
        "bundleId": "com.triviapay.app",
        "environment": "production",
        "purchaseDate": now_ms,
        "productType": "consumable",
    }


def build_apple_notification_payload(transaction_id=APPLE_TEST_TRANSACTION_ID):
    return {
        "notificationType": "REFUND",
        "notificationUUID": "notif-apple-1",
        "data": {"signedTransactionInfo": "tx-jws"},
    }


def build_google_purchase_response():
    return {
        "orderId": GOOGLE_TEST_TRANSACTION_ID,
        "productId": TEST_PRODUCT_GEMS,
        "purchaseState": 0,
        "acknowledgementState": 0,
        "purchaseTimeMillis": "1700000000000",
    }


def build_google_rtdn_payload(purchase_token=GOOGLE_TEST_PURCHASE_TOKEN):
    data = {
        "oneTimeProductNotification": {
            "notificationType": 2,
            "purchaseToken": purchase_token,
            "sku": TEST_PRODUCT_GEMS,
        }
    }
    return {
        "message": {
            "messageId": "msg-1",
            "data": base64.b64encode(json.dumps(data).encode("utf-8")).decode("utf-8"),
        }
    }


@pytest.mark.asyncio
async def test_apple_idempotency_same_transaction(async_session_maker, monkeypatch):
    async with async_session_maker() as session:
        await seed_user_and_product(session)

    monkeypatch.setattr(config, "APPLE_APP_BUNDLE_ID", "com.triviapay.app")

    def fake_verify(jws):
        return build_apple_payload()

    monkeypatch.setattr(apple_iap_service, "verify_signed_transaction_info", fake_verify)
    monkeypatch.setattr(payments_service, "verify_signed_transaction_info", fake_verify)

    async with async_session_maker() as session1:
        user1 = await get_user(session1)
        result1 = await process_apple_iap(
            session1,
            user1,
            signed_transaction_info="tx-jws",
            product_id=TEST_PRODUCT_GEMS,
            environment="production",
        )

    async with async_session_maker() as session2:
        user2 = await get_user(session2)
        result2 = await process_apple_iap(
            session2,
            user2,
            signed_transaction_info="tx-jws",
            product_id=TEST_PRODUCT_GEMS,
            environment="production",
        )

    assert result1["already_processed"] is False
    assert result2["already_processed"] is True

    async with async_session_maker() as session3:
        user = await get_user(session3)
        receipts = (await session3.execute(select(IapReceipt))).scalars().all()
        transactions = (await session3.execute(select(WalletTransaction))).scalars().all()

    assert user.wallet_balance_minor == 499
    assert len(receipts) == 1
    assert len(transactions) == 1


@pytest.mark.asyncio
async def test_google_idempotency_same_token(async_session_maker, monkeypatch):
    async with async_session_maker() as session:
        await seed_user_and_product(session)

    async def fake_verify_google_purchase_token(*args, **kwargs):
        return build_google_purchase_response()

    monkeypatch.setattr(
        google_iap_service, "verify_google_purchase_token", fake_verify_google_purchase_token
    )
    monkeypatch.setattr(google_iap_service, "consume_google_purchase", AsyncMock())
    monkeypatch.setattr(google_iap_service, "acknowledge_google_purchase", AsyncMock())

    async with async_session_maker() as session1:
        user1 = await get_user(session1)
        result1 = await process_google_iap(
            session1,
            user1,
            package_name="com.triviapay.app",
            product_id=TEST_PRODUCT_GEMS,
            purchase_token=GOOGLE_TEST_PURCHASE_TOKEN,
        )

    async with async_session_maker() as session2:
        user2 = await get_user(session2)
        result2 = await process_google_iap(
            session2,
            user2,
            package_name="com.triviapay.app",
            product_id=TEST_PRODUCT_GEMS,
            purchase_token=GOOGLE_TEST_PURCHASE_TOKEN,
        )

    assert result1["already_processed"] is False
    assert result2["already_processed"] is True

    async with async_session_maker() as session3:
        user = await get_user(session3)
        receipts = (await session3.execute(select(IapReceipt))).scalars().all()
        transactions = (await session3.execute(select(WalletTransaction))).scalars().all()

    assert user.wallet_balance_minor == 499
    assert len(receipts) == 1
    assert len(transactions) == 1


@pytest.mark.asyncio
async def test_concurrency_race_same_google_token(async_session_maker, monkeypatch):
    async with async_session_maker() as session:
        await seed_user_and_product(session)

    async def fake_verify_google_purchase_token(*args, **kwargs):
        return build_google_purchase_response()

    async def delayed_adjust(*args, **kwargs):
        await asyncio.sleep(0.05)
        return await adjust_wallet_balance(*args, **kwargs)

    monkeypatch.setattr(
        google_iap_service, "verify_google_purchase_token", fake_verify_google_purchase_token
    )
    monkeypatch.setattr(google_iap_service, "adjust_wallet_balance", delayed_adjust)
    monkeypatch.setattr(google_iap_service, "consume_google_purchase", AsyncMock())
    monkeypatch.setattr(google_iap_service, "acknowledge_google_purchase", AsyncMock())

    async def run_call():
        async with async_session_maker() as session:
            user = await get_user(session)
            return await process_google_iap(
                session,
                user,
                package_name="com.triviapay.app",
                product_id=TEST_PRODUCT_GEMS,
                purchase_token=GOOGLE_TEST_PURCHASE_TOKEN,
            )

    results = await asyncio.gather(run_call(), run_call())
    already_processed = [r.get("already_processed") for r in results if isinstance(r, dict)]
    assert any(already_processed)

    async with async_session_maker() as session:
        user = await get_user(session)
        receipts = (await session.execute(select(IapReceipt))).scalars().all()
        transactions = (await session.execute(select(WalletTransaction))).scalars().all()

    assert user.wallet_balance_minor == 499
    assert len(receipts) == 1
    assert len(transactions) == 1


@pytest.mark.asyncio
async def test_duplicate_apple_webhook_delivery(async_session_maker, monkeypatch):
    async with async_session_maker() as session:
        await seed_user_and_product(session)
        user = await get_user(session)
        user.wallet_balance_minor = 499
        receipt = IapReceipt(
            user_id=user.account_id,
            platform="apple",
            transaction_id=APPLE_TEST_TRANSACTION_ID,
            product_id=TEST_PRODUCT_GEMS,
            status="credited",
            credited_amount_minor=499,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(receipt)
        await session.commit()

    def fake_verify(jws):
        if jws == "notif-jws":
            return build_apple_notification_payload()
        return build_apple_payload()

    monkeypatch.setattr(apple_iap_service, "verify_signed_transaction_info", fake_verify)
    monkeypatch.setattr(payments_service, "verify_signed_transaction_info", fake_verify)

    async with async_session_maker() as session1:
        first = await process_apple_notification(session1, signed_payload="notif-jws")

    async with async_session_maker() as session2:
        second = await process_apple_notification(session2, signed_payload="notif-jws")

    assert first["status"] == "processed"
    assert second["status"] == "already_processed"

    async with async_session_maker() as session3:
        user = await get_user(session3)
        refunds = (
            await session3.execute(
                select(WalletTransaction).where(WalletTransaction.kind == "iap_refund")
            )
        ).scalars().all()

    assert user.wallet_balance_minor == 0
    assert len(refunds) == 1


@pytest.mark.asyncio
async def test_duplicate_google_webhook_delivery(async_session_maker):
    async with async_session_maker() as session:
        await seed_user_and_product(session)
        user = await get_user(session)
        user.wallet_balance_minor = 499
        receipt = IapReceipt(
            user_id=user.account_id,
            platform="google",
            transaction_id=GOOGLE_TEST_TRANSACTION_ID,
            product_id=TEST_PRODUCT_GEMS,
            purchase_token=GOOGLE_TEST_PURCHASE_TOKEN,
            status="credited",
            credited_amount_minor=499,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(receipt)
        await session.commit()

    payload = build_google_rtdn_payload()

    async with async_session_maker() as session1:
        first = await process_google_notification(session1, payload=payload)

    async with async_session_maker() as session2:
        second = await process_google_notification(session2, payload=payload)

    assert first["status"] == "processed"
    assert second["status"] == "already_processed"

    async with async_session_maker() as session3:
        user = await get_user(session3)
        refunds = (
            await session3.execute(
                select(WalletTransaction).where(WalletTransaction.kind == "iap_refund")
            )
        ).scalars().all()

    assert user.wallet_balance_minor == 0
    assert len(refunds) == 1


@pytest.mark.asyncio
async def test_webhook_before_verify_blocks_apple(async_session_maker, monkeypatch):
    async with async_session_maker() as session:
        await seed_user_and_product(session)

    def fake_verify(jws):
        if jws == "notif-jws":
            return build_apple_notification_payload()
        return build_apple_payload()

    monkeypatch.setattr(apple_iap_service, "verify_signed_transaction_info", fake_verify)
    monkeypatch.setattr(payments_service, "verify_signed_transaction_info", fake_verify)
    monkeypatch.setattr(config, "APPLE_APP_BUNDLE_ID", "com.triviapay.app")

    async with async_session_maker() as session1:
        await process_apple_notification(session1, signed_payload="notif-jws")

    async with async_session_maker() as session2:
        user = await get_user(session2)
        with pytest.raises(HTTPException) as exc:
            await process_apple_iap(
                session2,
                user,
                signed_transaction_info="tx-jws",
                product_id=TEST_PRODUCT_GEMS,
                environment="production",
            )
    assert "revoked" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_webhook_before_verify_blocks_google(async_session_maker, monkeypatch):
    async with async_session_maker() as session:
        await seed_user_and_product(session)

    payload = build_google_rtdn_payload()

    async with async_session_maker() as session1:
        await process_google_notification(session1, payload=payload)

    mocked_verify = AsyncMock(return_value=build_google_purchase_response())
    monkeypatch.setattr(google_iap_service, "verify_google_purchase_token", mocked_verify)

    async with async_session_maker() as session2:
        user = await get_user(session2)
        with pytest.raises(HTTPException) as exc:
            await process_google_iap(
                session2,
                user,
                package_name="com.triviapay.app",
                product_id=TEST_PRODUCT_GEMS,
                purchase_token=GOOGLE_TEST_PURCHASE_TOKEN,
            )
    assert "revoked" in str(exc.value.detail).lower()
    assert mocked_verify.await_count == 0


@pytest.mark.asyncio
async def test_mocked_apple_jws_claim_validation(async_session_maker, monkeypatch):
    async with async_session_maker() as session:
        await seed_user_and_product(session)

    def fake_verify(jws):
        payload = build_apple_payload()
        payload["bundleId"] = "com.other.app"
        return payload

    monkeypatch.setattr(apple_iap_service, "verify_signed_transaction_info", fake_verify)
    monkeypatch.setattr(config, "APPLE_APP_BUNDLE_ID", "com.triviapay.app")

    async with async_session_maker() as session2:
        user = await get_user(session2)
        with pytest.raises(HTTPException) as exc:
            await process_apple_iap(
                session2,
                user,
                signed_transaction_info="tx-jws",
                product_id=TEST_PRODUCT_GEMS,
                environment="production",
            )
    assert "bundle id" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_mocked_google_api_ack_consume(async_session_maker, monkeypatch):
    async with async_session_maker() as session:
        await seed_user_and_product(session)

    async def fake_verify_google_purchase_token(*args, **kwargs):
        return build_google_purchase_response()

    mock_consume = AsyncMock()
    mock_ack = AsyncMock()

    monkeypatch.setattr(
        google_iap_service, "verify_google_purchase_token", fake_verify_google_purchase_token
    )
    monkeypatch.setattr(google_iap_service, "consume_google_purchase", mock_consume)
    monkeypatch.setattr(google_iap_service, "acknowledge_google_purchase", mock_ack)

    async with async_session_maker() as session2:
        user = await get_user(session2)
        result = await process_google_iap(
            session2,
            user,
            package_name="com.triviapay.app",
            product_id=TEST_PRODUCT_GEMS,
            purchase_token=GOOGLE_TEST_PURCHASE_TOKEN,
        )

    assert result["success"] is True
    assert mock_consume.await_count == 1
    assert mock_ack.await_count == 0
