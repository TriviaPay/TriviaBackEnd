import importlib
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import get_db
from models import User


class _DummyMgmtUser:
    def __init__(self):
        self.updated = []

    def load(self, user_id):
        return {"user": {"password": False}}

    def update(self, login_id, **kwargs):
        self.updated.append((login_id, kwargs))

    def create(self, **kwargs):
        self.updated.append(("create", kwargs))

    def set_active_password(self, login_id, password):
        return None


class _DummyMgmt:
    def __init__(self):
        self.user = _DummyMgmtUser()


class _DummyClient:
    def __init__(self):
        self.mgmt = _DummyMgmt()



def _make_client(test_db, login_module):
    app = FastAPI()
    app.include_router(login_module.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_username_and_email_available_case_insensitive(test_db, monkeypatch):
    login = importlib.import_module("routers.login")
    login = importlib.reload(login)

    with _make_client(test_db, login) as client:
        username_response = client.get("/username-available", params={"username": "TestUser1"})
        email_response = client.get("/email-available", params={"email": "TEST1@EXAMPLE.COM"})

    assert username_response.status_code == 200
    assert username_response.json()["available"] is False
    assert email_response.status_code == 200
    assert email_response.json()["available"] is False


def test_bind_password_updates_existing_user(test_db, monkeypatch):
    login = importlib.import_module("routers.login")
    login = importlib.reload(login)

    monkeypatch.setattr(login, "mgmt_client", _DummyClient())
    monkeypatch.setattr(login, "STORE_PASSWORD_IN_DESCOPE", False)
    monkeypatch.setattr(login, "STORE_PASSWORD_IN_NEONDB", True)
    monkeypatch.setattr(
        login,
        "validate_descope_jwt",
        lambda token: {"userId": "descope-1", "loginIds": ["test1@example.com"]},
    )

    user = test_db.query(User).filter(User.email == "test1@example.com").first()
    assert user is not None

    payload = {
        "email": "test1@example.com",
        "password": "Password123",
        "username": "UpdatedUser",
        "country": "United States",
        "date_of_birth": "2000-01-01",
    }
    headers = {"Authorization": "Bearer token"}

    with _make_client(test_db, login) as client:
        response = client.post("/bind-password", json=payload, headers=headers)

    assert response.status_code == 200
    assert response.json()["success"] is True

    updated = test_db.query(User).filter(User.email == "test1@example.com").first()
    assert updated.username == "UpdatedUser"
    assert updated.country == "United States"
    assert updated.descope_user_id == "descope-1"
    assert updated.password is not None
