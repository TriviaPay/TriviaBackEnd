import json
from contextlib import contextmanager
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

import core.security as auth
import routers.messaging.dm_sse as dm_sse
from models import User, UserPresence


def _make_client(monkeypatch):
    app = FastAPI()
    app.include_router(dm_sse.router)

    monkeypatch.setattr(dm_sse, "E2EE_DM_ENABLED", True)
    return TestClient(app)


def test_sse_rejects_query_token_by_default(monkeypatch):
    monkeypatch.setattr(dm_sse, "E2EE_DM_SSE_ALLOW_QUERY_TOKEN", False)
    with _make_client(monkeypatch) as client:
        response = client.get("/dm/sse?token=abc")
    assert response.status_code == 401
    assert response.json()["detail"] == "Use Authorization header for SSE"


def test_sse_streams_message(monkeypatch):
    async def fake_subscribe_dm_user(_user_id):
        yield json.dumps({"type": "dm", "message": "hello"})

    calls = {"count": 0}

    async def fake_is_disconnected(self):
        calls["count"] += 1
        return calls["count"] > 1

    monkeypatch.setattr(dm_sse, "E2EE_DM_SSE_ALLOW_QUERY_TOKEN", True)
    monkeypatch.setattr(dm_sse, "SSE_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(dm_sse, "REDIS_RETRY_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(dm_sse, "get_redis", lambda: object())
    monkeypatch.setattr(dm_sse, "subscribe_dm_user", fake_subscribe_dm_user)
    monkeypatch.setattr(dm_sse, "_load_user_context", lambda _token: (123, []))
    monkeypatch.setattr(Request, "is_disconnected", fake_is_disconnected, raising=False)

    with _make_client(monkeypatch) as client:
        with client.stream("GET", "/dm/sse?token=abc") as response:
            chunks = []
            for chunk in response.iter_text():
                chunks.append(chunk)
                if len(chunks) >= 2:
                    break

    assert response.status_code == 200
    assert any("retry: 5000" in chunk for chunk in chunks)
    assert any("data:" in chunk for chunk in chunks)


def test_get_token_expiry_and_missing_token(monkeypatch):
    monkeypatch.setattr(auth, "decode_jwt_payload", lambda _token: {"exp": 123})
    assert dm_sse.get_token_expiry("token") == 123

    monkeypatch.setattr(
        auth,
        "decode_jwt_payload",
        lambda _token: (_ for _ in ()).throw(Exception("bad")),
    )
    assert dm_sse.get_token_expiry("token") is None

    with pytest.raises(Exception):
        dm_sse.get_user_from_token(None, None)


def test_update_presence_creates_and_updates(test_db, monkeypatch):
    user = test_db.query(User).first()

    @contextmanager
    def fake_db_context():
        yield test_db

    monkeypatch.setattr(dm_sse, "get_db_context", fake_db_context)
    monkeypatch.setattr(dm_sse, "PRESENCE_ENABLED", True)

    now = datetime.utcnow()
    dm_sse._update_presence(user.account_id, now, True, True)
    presence = (
        test_db.query(UserPresence)
        .filter(UserPresence.user_id == user.account_id)
        .first()
    )
    assert presence is not None
    assert presence.device_online is True

    later = datetime.utcnow()
    dm_sse._update_presence(user.account_id, later, None, False)
    presence = (
        test_db.query(UserPresence)
        .filter(UserPresence.user_id == user.account_id)
        .first()
    )
    assert presence.last_seen_at == later
