import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import get_db
from models import OneSignalPlayer, User
from routers.dependencies import get_current_user


def _make_client(test_db, onesignal_module, user, monkeypatch):
    app = FastAPI()
    app.include_router(onesignal_module.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(onesignal_module, "ONESIGNAL_ENABLED", True)
    return TestClient(app)


def test_register_player_new_and_list(test_db, monkeypatch):
    onesignal = importlib.import_module("routers.onesignal")
    onesignal = importlib.reload(onesignal)

    user = test_db.query(User).first()

    with _make_client(test_db, onesignal, user, monkeypatch) as client:
        response = client.post(
            "/onesignal/register",
            json={"player_id": "player-1", "platform": "ios"},
        )
        assert response.status_code == 200

        list_response = client.get("/onesignal/players", params={"limit": 10, "offset": 0})

    assert list_response.status_code == 200
    payload = list_response.json()
    assert payload["total"] == 1
    assert payload["players"][0]["player_id"] == "player-1"


def test_register_player_hijack_prevented(test_db, monkeypatch):
    onesignal = importlib.import_module("routers.onesignal")
    onesignal = importlib.reload(onesignal)

    user1 = test_db.query(User).first()
    user2 = test_db.query(User).filter(User.account_id != user1.account_id).first()

    test_db.add(OneSignalPlayer(user_id=user1.account_id, player_id="player-2", platform="ios", is_valid=True))
    test_db.commit()

    with _make_client(test_db, onesignal, user2, monkeypatch) as client:
        response = client.post(
            "/onesignal/register",
            json={"player_id": "player-2", "platform": "ios"},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "Player ID is already registered to another user"


def test_register_player_rate_limited(test_db, monkeypatch):
    onesignal = importlib.import_module("routers.onesignal")
    onesignal = importlib.reload(onesignal)

    user = test_db.query(User).first()
    monkeypatch.setattr(onesignal, "_check_rate_limit", lambda *_args, **_kwargs: False)

    with _make_client(test_db, onesignal, user, monkeypatch) as client:
        response = client.post(
            "/onesignal/register",
            json={"player_id": "player-3", "platform": "ios"},
        )

    assert response.status_code == 429
