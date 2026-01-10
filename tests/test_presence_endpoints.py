import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import get_db
from models import User, UserPresence
from routers.dependencies import get_current_user
from routers.messaging import presence as presence_router


@pytest.fixture
def current_user(test_db):
    return test_db.query(User).first()


@pytest.fixture
def client(test_db, current_user, monkeypatch):
    app = FastAPI()
    app.include_router(presence_router.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: current_user
    monkeypatch.setattr(presence_router, "PRESENCE_ENABLED", True)

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides = {}


def test_get_presence_returns_defaults_without_write(client, test_db, current_user):
    response = client.get("/presence")
    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == current_user.account_id
    assert payload["device_online"] is False
    assert payload["privacy_settings"]["share_last_seen"] == "contacts"
    assert (
        test_db.query(UserPresence)
        .filter(UserPresence.user_id == current_user.account_id)
        .count()
        == 0
    )


def test_update_presence_creates_row_and_normalizes_all(client, test_db, current_user):
    response = client.patch(
        "/presence",
        json={"share_last_seen": "all", "share_online": False, "read_receipts": False},
    )
    assert response.status_code == 200
    assert response.json()["privacy_settings"]["share_last_seen"] == "everyone"

    presence = (
        test_db.query(UserPresence)
        .filter(UserPresence.user_id == current_user.account_id)
        .first()
    )
    assert presence is not None
    assert presence.privacy_settings["share_last_seen"] == "everyone"
    assert presence.privacy_settings["share_online"] is False
    assert presence.privacy_settings["read_receipts"] is False

    response = client.get("/presence")
    assert response.status_code == 200
    assert response.json()["privacy_settings"]["share_last_seen"] == "everyone"
