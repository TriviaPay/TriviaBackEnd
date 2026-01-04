import importlib
import sys
import types
from datetime import date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import get_db
from models import OneSignalPlayer, TriviaUserFreeModeDaily, User


def _make_client(test_db, internal_module):
    app = FastAPI()
    app.include_router(internal_module.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_trivia_reminder_queued(test_db, monkeypatch):
    internal = importlib.import_module("routers.internal")
    internal = importlib.reload(internal)

    today = date.today()
    fake_trivia = types.ModuleType("routers.trivia")
    fake_trivia.get_active_draw_date = lambda: today
    monkeypatch.setitem(sys.modules, "routers.trivia", fake_trivia)

    monkeypatch.setattr(internal, "ONESIGNAL_ENABLED", True)
    monkeypatch.setattr(internal, "_send_trivia_reminder_job", lambda *args, **kwargs: None)
    monkeypatch.setenv("INTERNAL_SECRET", "secret")

    import config
    monkeypatch.setattr(config, "ONESIGNAL_APP_ID", "app")
    monkeypatch.setattr(config, "ONESIGNAL_REST_API_KEY", "key")

    user1 = test_db.query(User).first()
    user2 = test_db.query(User).filter(User.account_id != user1.account_id).first()
    user1.notification_on = True
    user2.notification_on = True

    test_db.add_all([
        OneSignalPlayer(user_id=user1.account_id, player_id="player-1", platform="ios", is_valid=True),
        OneSignalPlayer(user_id=user2.account_id, player_id="player-2", platform="android", is_valid=True),
    ])
    test_db.add(
        TriviaUserFreeModeDaily(
            account_id=user1.account_id,
            date=today,
            question_order=3,
            question_id=1,
            status="answered_correct",
            third_question_completed_at=datetime.utcnow(),
        )
    )
    test_db.commit()

    with _make_client(test_db, internal) as client:
        response = client.post(
            "/internal/trivia-reminder",
            json={"only_incomplete_users": True},
            headers={"X-Secret": "secret"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["targeted_players"] == 1
    assert payload["targeted_users"] == 1


def test_internal_secret_required(test_db, monkeypatch):
    internal = importlib.import_module("routers.internal")
    internal = importlib.reload(internal)

    monkeypatch.setattr(internal, "ONESIGNAL_ENABLED", True)
    monkeypatch.setenv("INTERNAL_SECRET", "secret")

    with _make_client(test_db, internal) as client:
        response = client.post(
            "/internal/trivia-reminder",
            json={"only_incomplete_users": True},
            headers={"X-Secret": "bad"},
        )

    assert response.status_code == 401
