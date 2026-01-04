from datetime import datetime, date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import get_db
from models import User
from routers.dependencies import get_current_user
import routers.draw as draw


def _build_client(test_db):
    user = test_db.query(User).first()
    app = FastAPI()
    app.include_router(draw.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user

    return TestClient(app)


def test_draw_next_uses_cache_and_isoformat(monkeypatch, test_db):
    fixed_time = datetime(2024, 1, 2, 3, 4, 5)
    fixed_date = date(2024, 1, 2)
    calls = {"count": 0}

    def fake_calculate_prize_pool(db, day, commit_revenue=False):
        calls["count"] += 1
        return 1234

    monkeypatch.setattr(draw, "get_next_draw_time", lambda: fixed_time)
    monkeypatch.setattr(draw, "get_today_in_app_timezone", lambda: fixed_date)
    monkeypatch.setattr(draw, "calculate_prize_pool", fake_calculate_prize_pool)
    monkeypatch.setattr(draw, "_PRIZE_POOL_CACHE", {"date": None, "value": None, "expires_at": None})
    monkeypatch.setattr(draw, "_PRIZE_POOL_TTL_SECONDS", 300)

    with _build_client(test_db) as client:
        first = client.get("/draw/next")
        second = client.get("/draw/next")

    assert first.status_code == 200
    payload = first.json()
    assert payload["next_draw_time"] == fixed_time.isoformat()
    assert payload["prize_pool"] == 1234
    assert calls["count"] == 1
    assert second.status_code == 200

