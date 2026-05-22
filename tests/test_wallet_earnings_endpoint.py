from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.db import get_async_db
from app.dependencies import get_current_user
from app.models.user import User
from main import app


@pytest.fixture
def mock_user():
    user = MagicMock(spec=User)
    user.account_id = 12345
    user.wallet_currency = "usd"
    return user


def _make_user(account_id: int):
    user = MagicMock(spec=User)
    user.account_id = account_id
    user.wallet_currency = "usd"
    return user


@pytest.mark.asyncio
async def test_wallet_earnings_returns_latest_first_breakdown(mock_user):
    mock_db_session = AsyncMock()
    rows = [
        SimpleNamespace(
            draw_date=date(2026, 4, 5),
            amount_usd=3.25,
            subscription_type="silver",
            subscription_name="Silver Mode",
            subscription_amount_usd=10.0,
        ),
        SimpleNamespace(
            draw_date=date(2026, 4, 4),
            amount_usd=1.50,
            subscription_type="bronze",
            subscription_name="Bronze Mode",
            subscription_amount_usd=5.0,
        ),
        SimpleNamespace(
            draw_date=date(2026, 4, 2),
            amount_usd=2.00,
            subscription_type="silver",
            subscription_name="Silver Mode",
            subscription_amount_usd=10.0,
        ),
    ]

    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_async_db] = lambda: mock_db_session

    with patch(
        "app.routers.payments.service.payments_repository.list_user_draw_earnings",
        new=AsyncMock(return_value=rows),
    ):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                "/api/v1/wallet/earnings",
                headers={"Authorization": "Bearer test_token"},
            )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()

    assert payload["currency"] == "usd"
    assert payload["total_winnings_amount_usd"] == 6.75
    assert [item["date"] for item in payload["earnings"]] == [
        "2026-04-05",
        "2026-04-04",
        "2026-04-02",
    ]
    assert [item["subscription_type"] for item in payload["earnings"]] == [
        "silver",
        "bronze",
        "silver",
    ]

    totals = {
        item["subscription_type"]: item["total_winnings_amount_usd"]
        for item in payload["subscription_totals"]
    }
    assert totals == {"bronze": 1.5, "silver": 5.25}


@pytest.mark.asyncio
async def test_wallet_earnings_returns_zero_totals_when_empty(mock_user):
    mock_db_session = AsyncMock()

    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_async_db] = lambda: mock_db_session

    with patch(
        "app.routers.payments.service.payments_repository.list_user_draw_earnings",
        new=AsyncMock(return_value=[]),
    ):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                "/api/v1/wallet/earnings",
                headers={"Authorization": "Bearer test_token"},
            )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()

    assert payload["currency"] == "usd"
    assert payload["total_winnings_amount_usd"] == 0.0
    assert payload["earnings"] == []
    assert payload["subscription_totals"] == [
        {
            "subscription_type": "bronze",
            "subscription_name": "Bronze Mode",
            "subscription_amount_usd": 5.0,
            "total_winnings_amount_usd": 0.0,
        },
        {
            "subscription_type": "silver",
            "subscription_name": "Silver Mode",
            "subscription_amount_usd": 10.0,
            "total_winnings_amount_usd": 0.0,
        },
    ]


@pytest.mark.asyncio
async def test_wallet_earnings_handles_multiple_user_ids():
    mock_db_session = AsyncMock()
    current_user = {"value": _make_user(101)}
    rows_by_user_id = {
        101: [
            SimpleNamespace(
                draw_date=date(2026, 4, 8),
                amount_usd=4.00,
                subscription_type="silver",
                subscription_name="Silver Mode",
                subscription_amount_usd=10.0,
            ),
            SimpleNamespace(
                draw_date=date(2026, 4, 7),
                amount_usd=1.25,
                subscription_type="bronze",
                subscription_name="Bronze Mode",
                subscription_amount_usd=5.0,
            ),
        ],
        202: [
            SimpleNamespace(
                draw_date=date(2026, 4, 6),
                amount_usd=2.75,
                subscription_type="bronze",
                subscription_name="Bronze Mode",
                subscription_amount_usd=5.0,
            ),
        ],
        303: [],
    }
    seen_user_ids = []

    async def fake_list_user_draw_earnings(db, *, user_id: int):
        assert db is mock_db_session
        seen_user_ids.append(user_id)
        return rows_by_user_id[user_id]

    app.dependency_overrides[get_current_user] = lambda: current_user["value"]
    app.dependency_overrides[get_async_db] = lambda: mock_db_session

    with patch(
        "app.routers.payments.service.payments_repository.list_user_draw_earnings",
        new=fake_list_user_draw_earnings,
    ):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                "/api/v1/wallet/earnings",
                headers={"Authorization": "Bearer test_token"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["total_winnings_amount_usd"] == 5.25
            assert [item["subscription_type"] for item in payload["earnings"]] == [
                "silver",
                "bronze",
            ]

            current_user["value"] = _make_user(202)
            response = await ac.get(
                "/api/v1/wallet/earnings",
                headers={"Authorization": "Bearer test_token"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["total_winnings_amount_usd"] == 2.75
            assert payload["earnings"] == [
                {
                    "date": "2026-04-06",
                    "amount_usd": 2.75,
                    "subscription_type": "bronze",
                    "subscription_name": "Bronze Mode",
                    "subscription_amount_usd": 5.0,
                }
            ]

            current_user["value"] = _make_user(303)
            response = await ac.get(
                "/api/v1/wallet/earnings",
                headers={"Authorization": "Bearer test_token"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["total_winnings_amount_usd"] == 0.0
            assert payload["earnings"] == []

    app.dependency_overrides.clear()

    assert seen_user_ids == [101, 202, 303]
