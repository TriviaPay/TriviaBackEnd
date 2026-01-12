from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.db as db_module
import routers.messaging.global_chat as global_chat
from core.db import get_db
from models import (
    ChatMutePreferences,
    GlobalChatMessage,
    GlobalChatViewer,
    OneSignalPlayer,
    User,
)
from routers.dependencies import get_current_user
from routers.messaging.global_chat import send_push_for_global_chat_sync


@pytest.fixture
def current_user(test_db):
    return test_db.query(User).first()


@pytest.fixture
def client(test_db, current_user, monkeypatch):
    app = FastAPI()
    app.include_router(global_chat.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: current_user
    monkeypatch.setattr(global_chat, "get_chat_redis", AsyncMock(return_value=None))
    monkeypatch.setattr(global_chat, "check_rate_limit", AsyncMock(return_value=True))
    monkeypatch.setattr(global_chat, "check_burst_limit", AsyncMock(return_value=True))

    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides = {}


def test_global_chat_messages_include_reply(client, test_db, current_user):
    base_message = GlobalChatMessage(
        user_id=current_user.account_id,
        message="Base message",
        created_at=datetime.utcnow(),
    )
    test_db.add(base_message)
    test_db.commit()

    reply_message = GlobalChatMessage(
        user_id=current_user.account_id,
        message="Reply message",
        created_at=datetime.utcnow(),
        reply_to_message_id=base_message.id,
    )
    test_db.add(reply_message)
    test_db.commit()

    response = client.get("/global-chat/messages?limit=10")

    assert response.status_code == 200
    payload = response.json()
    reply = next(msg for msg in payload["messages"] if msg["id"] == reply_message.id)
    assert reply["reply_to"]["message_id"] == base_message.id


def test_global_chat_cleanup_requires_admin(client, current_user):
    current_user.is_admin = False
    response = client.post("/global-chat/cleanup")
    assert response.status_code == 403


def test_global_chat_reply_not_found(client):
    response = client.post(
        "/global-chat/send",
        json={"message": "Hello", "reply_to_message_id": 9999},
    )

    assert response.status_code == 404


def test_global_chat_pagination_before(client, test_db, current_user):
    messages = []
    base_time = datetime.utcnow()
    for idx in range(3):
        msg = GlobalChatMessage(
            user_id=current_user.account_id,
            message=f"Message {idx}",
            created_at=base_time + timedelta(seconds=idx),
        )
        test_db.add(msg)
        messages.append(msg)
    test_db.commit()

    before_id = messages[1].id
    response = client.get(f"/global-chat/messages?limit=10&before={before_id}")

    assert response.status_code == 200
    payload = response.json()
    returned_ids = {msg["id"] for msg in payload["messages"]}
    assert before_id not in returned_ids
    assert len(returned_ids) >= 1


def test_global_chat_messages_updates_viewer(client, test_db, current_user):
    response = client.get("/global-chat/messages?limit=10")

    assert response.status_code == 200
    viewer = (
        test_db.query(GlobalChatViewer)
        .filter(GlobalChatViewer.user_id == current_user.account_id)
        .first()
    )
    assert viewer is not None


def test_global_chat_push_respects_mutes(test_db):
    user1 = test_db.query(User).first()
    user2 = test_db.query(User).filter(User.account_id != user1.account_id).first()
    user3 = User(
        descope_user_id="test_user_3",
        email="test3@example.com",
        username="testuser3",
    )
    user4 = User(
        descope_user_id="test_user_4",
        email="test4@example.com",
        username="testuser4",
    )
    test_db.add_all([user3, user4])
    test_db.commit()
    test_db.add(
        ChatMutePreferences(
            user_id=user4.account_id,
            global_chat_muted=True,
            trivia_live_chat_muted=False,
        )
    )
    threshold = global_chat.ONESIGNAL_ACTIVITY_THRESHOLD_SECONDS
    test_db.add(
        OneSignalPlayer(
            user_id=user1.account_id,
            player_id="player-active",
            platform="ios",
            is_valid=True,
            last_active=datetime.utcnow(),
        )
    )
    test_db.add(
        OneSignalPlayer(
            user_id=user2.account_id,
            player_id="player-inactive",
            platform="ios",
            is_valid=True,
            last_active=datetime.utcnow() - timedelta(seconds=threshold + 10),
        )
    )
    test_db.add(
        OneSignalPlayer(
            user_id=user3.account_id,
            player_id="player-active-2",
            platform="ios",
            is_valid=True,
            last_active=datetime.utcnow(),
        )
    )
    test_db.add(
        OneSignalPlayer(
            user_id=user4.account_id,
            player_id="player-muted",
            platform="ios",
            is_valid=True,
            last_active=datetime.utcnow(),
        )
    )
    test_db.commit()

    async_mock = AsyncMock(return_value=True)

    def _override_get_db():
        yield test_db

    with patch.object(db_module, "get_db", _override_get_db), patch(
        "routers.messaging.global_chat.send_push_notification_async", async_mock
    ), patch(
        "utils.notification_storage.create_notifications_batch"
    ) as create_notifications_batch:
        send_push_for_global_chat_sync(
            message_id=1,
            sender_id=user1.account_id,
            sender_username="user1",
            message="Hello",
            created_at=datetime.utcnow(),
        )

    all_player_ids = [call.kwargs["player_ids"] for call in async_mock.call_args_list]
    flattened = [pid for batch in all_player_ids for pid in batch]
    assert "player-muted" not in flattened
    assert "player-active-2" in flattened
    assert "player-inactive" in flattened
    assert create_notifications_batch.called


class _FakeRedis:
    def __init__(self, cached_value):
        self.cached_value = cached_value
        self.set_calls = []

    async def get(self, key):
        return self.cached_value

    async def set(self, key, value, ex=None):
        self.set_calls.append((key, value, ex))


def test_global_chat_online_count_uses_cache(
    client, test_db, current_user, monkeypatch
):
    viewer = GlobalChatViewer(
        user_id=current_user.account_id,
        last_seen=datetime.utcnow(),
    )
    test_db.add(viewer)
    test_db.commit()

    redis = _FakeRedis("5")

    async def fake_get_chat_redis():
        return redis

    monkeypatch.setattr(global_chat, "get_chat_redis", fake_get_chat_redis)

    response = client.get("/global-chat/messages?limit=5")

    assert response.status_code == 200
    assert response.json()["online_count"] == 5
    assert redis.set_calls == []


def test_global_chat_online_count_sets_cache(
    client, test_db, current_user, monkeypatch
):
    viewer = GlobalChatViewer(
        user_id=current_user.account_id,
        last_seen=datetime.utcnow(),
    )
    test_db.add(viewer)
    test_db.commit()

    redis = _FakeRedis(None)

    async def fake_get_chat_redis():
        return redis

    monkeypatch.setattr(global_chat, "get_chat_redis", fake_get_chat_redis)

    response = client.get("/global-chat/messages?limit=5")

    assert response.status_code == 200
    assert response.json()["online_count"] == 1
    assert redis.set_calls


def test_get_display_username_fallbacks():
    user_with_email = SimpleNamespace(
        account_id=1, username="", email="user@example.com"
    )
    assert global_chat.get_display_username(user_with_email) == "user"

    user_without_email = SimpleNamespace(account_id=99, username=None, email=None)
    assert global_chat.get_display_username(user_without_email) == "User99"


def test_ensure_datetime_parsing():
    parsed = global_chat._ensure_datetime("2024-01-01T00:00:00")
    assert parsed.year == 2024

    fallback = global_chat._ensure_datetime("not-a-date")
    assert isinstance(fallback, datetime)


def test_publish_to_pusher_global_includes_reply(monkeypatch):
    captured = {}

    def fake_publish(channel, event, payload):
        captured["channel"] = channel
        captured["event"] = event
        captured["payload"] = payload

    monkeypatch.setattr(global_chat, "publish_chat_message_sync", fake_publish)

    global_chat.publish_to_pusher_global(
        message_id=5,
        user_id=10,
        username="tester",
        profile_pic=None,
        avatar_url=None,
        frame_url=None,
        badge=None,
        message="Hello",
        created_at="2024-01-01T00:00:00",
        reply_to={"message_id": 1},
    )

    assert captured["channel"] == "global-chat"
    assert captured["payload"]["reply_to"]["message_id"] == 1


def test_send_global_message_duplicate(client, test_db, current_user):
    existing = GlobalChatMessage(
        user_id=current_user.account_id,
        message="Hello",
        client_message_id="dup-1",
        created_at=datetime.utcnow(),
    )
    test_db.add(existing)
    test_db.commit()

    response = client.post(
        "/global-chat/send",
        json={"message": "Hello", "client_message_id": "dup-1"},
    )

    assert response.status_code == 200
    assert response.json()["duplicate"] is True


def test_send_global_message_success_with_reply(
    client, test_db, current_user, monkeypatch
):
    base_message = GlobalChatMessage(
        user_id=current_user.account_id,
        message="Base",
        created_at=datetime.utcnow(),
    )
    test_db.add(base_message)
    test_db.commit()

    monkeypatch.setattr(
        global_chat, "enqueue_chat_event", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        global_chat,
        "get_user_chat_profile_data",
        lambda user, db: {
            "profile_pic_url": None,
            "avatar_url": None,
            "frame_url": None,
            "badge": None,
        },
    )

    response = client.post(
        "/global-chat/send",
        json={"message": "Replying", "reply_to_message_id": base_message.id},
    )

    assert response.status_code == 200
    assert response.json()["duplicate"] is False


def test_send_global_message_burst_limit_fallback(
    client, test_db, current_user, monkeypatch
):
    monkeypatch.setattr(global_chat, "check_burst_limit", AsyncMock(return_value=None))
    monkeypatch.setattr(global_chat, "check_rate_limit", AsyncMock(return_value=True))
    monkeypatch.setattr(global_chat, "GLOBAL_CHAT_MAX_MESSAGES_PER_BURST", 1)
    monkeypatch.setattr(global_chat, "GLOBAL_CHAT_BURST_WINDOW_SECONDS", 60)

    test_db.add(
        GlobalChatMessage(
            user_id=current_user.account_id,
            message="Existing",
            created_at=datetime.utcnow(),
        )
    )
    test_db.commit()

    response = client.post("/global-chat/send", json={"message": "Another"})

    assert response.status_code == 429


def test_send_global_message_rate_limit_fallback(
    client, test_db, current_user, monkeypatch
):
    monkeypatch.setattr(global_chat, "check_burst_limit", AsyncMock(return_value=True))
    monkeypatch.setattr(global_chat, "check_rate_limit", AsyncMock(return_value=None))
    monkeypatch.setattr(global_chat, "GLOBAL_CHAT_MAX_MESSAGES_PER_MINUTE", 1)

    test_db.add(
        GlobalChatMessage(
            user_id=current_user.account_id,
            message="Existing",
            created_at=datetime.utcnow(),
        )
    )
    test_db.commit()

    response = client.post("/global-chat/send", json={"message": "Another"})

    assert response.status_code == 429


def test_global_chat_cleanup_admin(client, test_db, current_user, monkeypatch):
    current_user.is_admin = True
    monkeypatch.setattr(global_chat, "GLOBAL_CHAT_RETENTION_DAYS", 0)
    test_db.add(
        GlobalChatMessage(
            user_id=current_user.account_id,
            message="Old",
            created_at=datetime.utcnow() - timedelta(days=1),
        )
    )
    test_db.commit()

    response = client.post("/global-chat/cleanup")

    assert response.status_code == 200
    assert response.json()["deleted_count"] >= 1


def test_global_chat_disabled_paths(client, monkeypatch):
    monkeypatch.setattr(global_chat, "GLOBAL_CHAT_ENABLED", False)

    response = client.get("/global-chat/messages?limit=5")
    assert response.status_code == 403

    response = client.post("/global-chat/send", json={"message": "Hello"})
    assert response.status_code == 403

    response = client.post("/global-chat/cleanup")
    assert response.status_code == 403
