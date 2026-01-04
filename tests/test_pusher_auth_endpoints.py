import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import get_db
from models import User, PrivateChatConversation, Block
from routers.dependencies import get_current_user
from routers import pusher_auth as pusher_auth_router


class _PusherStub:
    def __init__(self):
        self.calls = []

    def authenticate(self, channel, socket_id, custom_data=None):
        self.calls.append((channel, socket_id, custom_data))
        return {"auth": "token"}


@pytest.fixture
def current_user(test_db):
    return test_db.query(User).first()


@pytest.fixture
def other_user(test_db, current_user):
    return test_db.query(User).filter(User.account_id != current_user.account_id).first()


@pytest.fixture
def client(test_db, current_user, monkeypatch):
    app = FastAPI()
    app.include_router(pusher_auth_router.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: current_user
    monkeypatch.setattr(pusher_auth_router, "PUSHER_ENABLED", True)
    monkeypatch.setattr(pusher_auth_router, "_AUTH_CACHE_TTL_SECONDS", 0)
    pusher_auth_router._conversation_cache.clear()
    pusher_auth_router._block_cache.clear()

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides = {}


def test_public_channel_skips_pusher_client(client, monkeypatch):
    def _boom():
        raise AssertionError("get_pusher_client should not be called for public channels")

    monkeypatch.setattr(pusher_auth_router, "get_pusher_client", _boom)
    response = client.post("/pusher/auth", data={
        "socket_id": "1.1",
        "channel_name": "global-chat"
    })
    assert response.status_code == 200
    assert response.json()["status"] == "authorized"


def test_presence_channel_scope_check(client, current_user, monkeypatch):
    stub = _PusherStub()
    monkeypatch.setattr(pusher_auth_router, "get_pusher_client", lambda: stub)

    response = client.post("/pusher/auth", data={
        "socket_id": "1.1",
        "channel_name": f"presence-user-{current_user.account_id}"
    })
    assert response.status_code == 200
    assert response.json()["auth"] == "token"

    response = client.post("/pusher/auth", data={
        "socket_id": "1.1",
        "channel_name": f"presence-user-{current_user.account_id + 1}"
    })
    assert response.status_code == 403


def test_private_channel_auth_and_blocking(client, test_db, current_user, other_user, monkeypatch):
    conversation = PrivateChatConversation(
        user1_id=current_user.account_id,
        user2_id=other_user.account_id,
        status="accepted",
        requested_by=current_user.account_id
    )
    test_db.add(conversation)
    test_db.commit()

    stub = _PusherStub()
    monkeypatch.setattr(pusher_auth_router, "get_pusher_client", lambda: stub)

    response = client.post("/pusher/auth", data={
        "socket_id": "1.1",
        "channel_name": f"private-conversation-{conversation.id}"
    })
    assert response.status_code == 200
    assert response.json()["auth"] == "token"

    conversation.status = "pending"
    test_db.commit()
    response = client.post("/pusher/auth", data={
        "socket_id": "1.1",
        "channel_name": f"private-conversation-{conversation.id}"
    })
    assert response.status_code == 403

    conversation.status = "accepted"
    test_db.add(Block(blocker_id=current_user.account_id, blocked_id=other_user.account_id))
    test_db.commit()
    response = client.post("/pusher/auth", data={
        "socket_id": "1.1",
        "channel_name": f"private-conversation-{conversation.id}"
    })
    assert response.status_code == 403
