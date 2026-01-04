from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from db import get_db
from models import User, Block
from routers.dependencies import get_current_user
import routers.dm_privacy as dm_privacy


def _make_client(test_db, user, monkeypatch):
    app = FastAPI()
    app.include_router(dm_privacy.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(dm_privacy, "E2EE_DM_ENABLED", True)
    return TestClient(app)


def test_block_unblock_and_list(test_db, monkeypatch):
    user = test_db.query(User).first()
    blocked_user = test_db.query(User).filter(User.account_id != user.account_id).first()

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.post("/dm/block", json={"blocked_user_id": blocked_user.account_id})
        assert response.status_code == 200

        response = client.get("/dm/blocks")
        assert response.status_code == 200
        blocks = response.json()["blocked_users"]
        assert len(blocks) == 1
        assert blocks[0]["user_id"] == blocked_user.account_id

        response = client.delete(f"/dm/block/{blocked_user.account_id}")
        assert response.status_code == 200

        response = client.get("/dm/blocks")
        assert response.status_code == 200
        assert response.json()["blocked_users"] == []


def test_block_integrity_error_returns_success(test_db, monkeypatch):
    user = test_db.query(User).first()
    blocked_user = test_db.query(User).filter(User.account_id != user.account_id).first()

    original_commit = test_db.commit

    def fake_commit():
        raise IntegrityError("duplicate", None, None)

    test_db.commit = fake_commit
    try:
        with _make_client(test_db, user, monkeypatch) as client:
            response = client.post("/dm/block", json={"blocked_user_id": blocked_user.account_id})
    finally:
        test_db.commit = original_commit

    assert response.status_code == 200
    assert response.json()["message"] == "User already blocked"
