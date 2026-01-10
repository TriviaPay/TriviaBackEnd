import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import get_db
from models import User
from routers.auth import refresh as refresh_router


@pytest.fixture
def client(test_db, monkeypatch):
    app = FastAPI()
    app.include_router(refresh_router.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    refresh_router._SESSION_CACHE.clear()

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides = {}


def test_refresh_requires_auth_header(client):
    response = client.post("/auth/refresh")
    assert response.status_code == 401


def test_refresh_success_existing_user(client, test_db):
    user = test_db.query(User).first()
    user.descope_user_id = "descope-123"
    test_db.commit()

    def _validate(_client, token):
        return {"userId": "descope-123", "loginIds": ["user@example.com"], "sub": "sub"}

    refresh_router._SESSION_CACHE.clear()
    refresh_router._validate_session_with_timeout = _validate

    response = client.post("/auth/refresh", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["access_token"] == "token"
    assert payload["user_info"]["userId"] == "descope-123"


def test_refresh_missing_user_returns_404(client, test_db):
    def _validate(_client, token):
        return {"userId": "missing-user", "loginIds": ["missing@example.com"]}

    refresh_router._SESSION_CACHE.clear()
    refresh_router._validate_session_with_timeout = _validate

    response = client.post("/auth/refresh", headers={"Authorization": "Bearer token"})
    assert response.status_code == 404


def test_refresh_timeout_path(client):
    def _timeout(_client, token):
        from fastapi import HTTPException

        raise HTTPException(status_code=504, detail="Session validation timed out")

    refresh_router._SESSION_CACHE.clear()
    refresh_router._validate_session_with_timeout = _timeout

    response = client.post("/auth/refresh", headers={"Authorization": "Bearer token"})
    assert response.status_code == 504


def test_refresh_fallback_leeway_path(client, test_db):
    user = test_db.query(User).first()
    user.descope_user_id = "descope-fallback"
    test_db.commit()

    call_state = {"count": 0}

    def _validate(_client, token):
        call_state["count"] += 1
        if call_state["count"] == 1:
            raise Exception("time glitch")
        return {"userId": "descope-fallback", "loginIds": ["fallback@example.com"]}

    refresh_router._SESSION_CACHE.clear()
    refresh_router._validate_session_with_timeout = _validate

    response = client.post("/auth/refresh", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
    assert response.json()["message"] == "Session validated with extended leeway"
