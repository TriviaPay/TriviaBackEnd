from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from core.db import get_db
from main import app
from models import GemPackageConfig, User, UserGemPurchase
from routers.dependencies import get_current_user


@pytest.fixture
def current_user(test_db):
    return test_db.query(User).first()


@pytest.fixture
def client(test_db, current_user):
    previous_overrides = app.dependency_overrides.copy()

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: current_user

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides = previous_overrides


def test_get_gem_packages_returns_prices(client, test_db):
    package = GemPackageConfig(
        product_id="GP001",
        price_minor=299,
        gems_amount=100,
        is_one_time=False,
        description="Starter pack",
    )
    test_db.add(package)
    test_db.commit()

    response = client.get("/store/gem-packages")

    assert response.status_code == 200
    payload = response.json()
    assert payload
    item = payload[0]
    assert item["id"] == package.id
    assert item["price_usd"] == 2.99
    assert item["gems_amount"] == 100
    assert item["is_one_time"] is False


def test_buy_gems_success(client, test_db, current_user):
    current_user.wallet_balance_minor = 700
    current_user.wallet_balance = 7.0
    current_user.gems = 0
    test_db.commit()

    package = GemPackageConfig(
        product_id="GP002",
        price_minor=500,
        gems_amount=50,
        is_one_time=False,
        description="Value pack",
    )
    test_db.add(package)
    test_db.commit()

    response = client.post("/store/buy-gems", json={"package_id": package.id})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["remaining_gems"] == 50
    assert payload["remaining_balance"] == 2.0

    test_db.refresh(current_user)
    assert current_user.wallet_balance_minor == 200
    assert current_user.gems == 50

    purchase = (
        test_db.query(UserGemPurchase)
        .filter(UserGemPurchase.user_id == current_user.account_id)
        .first()
    )
    assert purchase is not None
    assert purchase.package_id == package.id


def test_buy_gems_insufficient_balance(client, test_db, current_user):
    current_user.wallet_balance_minor = 100
    current_user.wallet_balance = 1.0
    test_db.commit()

    package = GemPackageConfig(
        product_id="GP003",
        price_minor=200,
        gems_amount=25,
        is_one_time=False,
        description="Small pack",
    )
    test_db.add(package)
    test_db.commit()

    response = client.post("/store/buy-gems", json={"package_id": package.id})

    assert response.status_code == 400


def test_buy_gems_one_time_prevent_repeat(client, test_db, current_user):
    current_user.wallet_balance_minor = 1000
    current_user.wallet_balance = 10.0
    test_db.commit()

    package = GemPackageConfig(
        product_id="GP004",
        price_minor=300,
        gems_amount=30,
        is_one_time=True,
        description="One-time pack",
    )
    test_db.add(package)
    test_db.flush()

    existing_purchase = UserGemPurchase(
        user_id=current_user.account_id,
        package_id=package.id,
        price_paid=3.0,
        gems_received=30,
        purchase_date=datetime.utcnow(),
    )
    test_db.add(existing_purchase)
    test_db.commit()

    response = client.post("/store/buy-gems", json={"package_id": package.id})

    assert response.status_code == 400


def test_buy_gems_package_not_found(client):
    response = client.post("/store/buy-gems", json={"package_id": 9999})

    assert response.status_code == 404


def test_buy_gems_uses_wallet_balance_fallback(client, test_db, current_user):
    current_user.wallet_balance_minor = None
    current_user.wallet_balance = 5.0
    current_user.gems = 0
    test_db.commit()

    package = GemPackageConfig(
        product_id="GP005",
        price_minor=500,
        gems_amount=10,
        is_one_time=False,
        description="Fallback pack",
    )
    test_db.add(package)
    test_db.commit()

    response = client.post("/store/buy-gems", json={"package_id": package.id})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["remaining_balance"] == 0.0
