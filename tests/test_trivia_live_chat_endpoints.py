from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytz
from fastapi.testclient import TestClient

import core.db as db_module
from core.db import get_db
from main import app
from models import (
    ChatMutePreferences,
    OneSignalPlayer,
    TriviaLiveChatLike,
    TriviaLiveChatMessage,
    User,
)
from routers.dependencies import get_current_user
from routers.trivia.trivia_live_chat import send_push_for_trivia_live_chat_sync


@pytest.fixture
def current_user(test_db):
    return test_db.query(User).first()


@pytest.fixture
def client(test_db, current_user):
    previous_overrides = app.dependency_overrides.copy()

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: current_user

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides = previous_overrides


def _patch_trivia_live_chat_window():
    tz = pytz.UTC
    now = datetime.now(tz)
    return (
        patch("routers.trivia.trivia_live_chat.TRIVIA_LIVE_CHAT_ENABLED", True),
        patch("routers.trivia.trivia_live_chat.TRIVIA_LIVE_CHAT_PRE_HOURS", 1),
        patch("routers.trivia.trivia_live_chat.TRIVIA_LIVE_CHAT_POST_HOURS", 1),
        patch("routers.trivia.trivia_live_chat.get_next_draw_time", return_value=now),
        patch(
            "routers.trivia.trivia_live_chat.is_trivia_live_chat_active",
            return_value=True,
        ),
        now,
    )


def test_trivia_live_chat_send_message(client, test_db):
    enabled_patch, pre_patch, post_patch, draw_patch, active_patch, now = (
        _patch_trivia_live_chat_window()
    )
    with enabled_patch, pre_patch, post_patch, draw_patch, active_patch, patch(
        "routers.trivia.trivia_live_chat.enqueue_chat_event",
        new=AsyncMock(return_value=True),
    ):
        response = client.post(
            "/trivia-live-chat/send", json={"message": "Hello live chat"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["duplicate"] is False

    draw_date = now.astimezone(pytz.UTC).replace(tzinfo=None).date()
    message = (
        test_db.query(TriviaLiveChatMessage)
        .filter(TriviaLiveChatMessage.id == payload["message_id"])
        .first()
    )
    assert message is not None
    assert message.draw_date == draw_date


def test_trivia_live_chat_messages_include_reply(client, test_db, current_user):
    enabled_patch, pre_patch, post_patch, draw_patch, active_patch, now = (
        _patch_trivia_live_chat_window()
    )
    window_time = now.astimezone(pytz.UTC).replace(tzinfo=None)

    with enabled_patch, pre_patch, post_patch, draw_patch, active_patch:
        base_message = TriviaLiveChatMessage(
            user_id=current_user.account_id,
            message="Base message",
            draw_date=window_time.date(),
            created_at=window_time - timedelta(minutes=1),
        )
        test_db.add(base_message)
        test_db.commit()

        reply_message = TriviaLiveChatMessage(
            user_id=current_user.account_id,
            message="Reply message",
            draw_date=window_time.date(),
            created_at=window_time,
            reply_to_message_id=base_message.id,
        )
        test_db.add(reply_message)
        test_db.commit()

        response = client.get("/trivia-live-chat/messages?limit=10")

    assert response.status_code == 200
    payload = response.json()
    messages = payload["messages"]
    assert len(messages) == 2
    reply = next(msg for msg in messages if msg["id"] == reply_message.id)
    assert reply["reply_to"]["message_id"] == base_message.id


def test_trivia_live_chat_send_idempotent(client):
    enabled_patch, pre_patch, post_patch, draw_patch, active_patch, _ = (
        _patch_trivia_live_chat_window()
    )
    with enabled_patch, pre_patch, post_patch, draw_patch, active_patch, patch(
        "routers.trivia.trivia_live_chat.enqueue_chat_event",
        new=AsyncMock(return_value=True),
    ):
        response = client.post(
            "/trivia-live-chat/send",
            json={"message": "Hello", "client_message_id": "msg-1"},
        )
        assert response.status_code == 200
        message_id = response.json()["message_id"]

        response = client.post(
            "/trivia-live-chat/send",
            json={"message": "Hello again", "client_message_id": "msg-1"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["duplicate"] is True
        assert payload["message_id"] == message_id


def test_trivia_live_chat_reply_not_found(client):
    enabled_patch, pre_patch, post_patch, draw_patch, active_patch, _ = (
        _patch_trivia_live_chat_window()
    )
    with enabled_patch, pre_patch, post_patch, draw_patch, active_patch, patch(
        "routers.trivia.trivia_live_chat.enqueue_chat_event",
        new=AsyncMock(return_value=True),
    ):
        response = client.post(
            "/trivia-live-chat/send",
            json={"message": "Hello", "reply_to_message_id": 9999},
        )

    assert response.status_code == 404


def test_trivia_live_chat_messages_inactive_window(client):
    with patch("routers.trivia.trivia_live_chat.TRIVIA_LIVE_CHAT_ENABLED", True), patch(
        "routers.trivia.trivia_live_chat.is_trivia_live_chat_active", return_value=False
    ):
        response = client.get("/trivia-live-chat/messages?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["messages"] == []
    assert payload["is_active"] is False


def test_trivia_live_chat_status_disabled(client):
    with patch("routers.trivia.trivia_live_chat.TRIVIA_LIVE_CHAT_ENABLED", False):
        response = client.get("/trivia-live-chat/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is False


def test_trivia_live_chat_like_idempotent(client, test_db, current_user):
    enabled_patch, pre_patch, post_patch, draw_patch, active_patch, now = (
        _patch_trivia_live_chat_window()
    )
    draw_date = now.astimezone(pytz.UTC).replace(tzinfo=None).date()
    test_db.add(
        TriviaLiveChatLike(
            user_id=current_user.account_id,
            draw_date=draw_date,
            message_id=None,
        )
    )
    test_db.commit()

    with enabled_patch, pre_patch, post_patch, draw_patch, active_patch:
        response = client.post("/trivia-live-chat/like")

    assert response.status_code == 200
    payload = response.json()
    assert payload["already_liked"] is True


def test_trivia_live_chat_likes_user_liked(client, test_db, current_user):
    enabled_patch, pre_patch, post_patch, draw_patch, active_patch, now = (
        _patch_trivia_live_chat_window()
    )
    draw_date = now.astimezone(pytz.UTC).replace(tzinfo=None).date()
    test_db.add(
        TriviaLiveChatLike(
            user_id=current_user.account_id,
            draw_date=draw_date,
            message_id=None,
        )
    )
    test_db.commit()

    with enabled_patch, pre_patch, post_patch, draw_patch, active_patch:
        response = client.get("/trivia-live-chat/likes")

    assert response.status_code == 200
    payload = response.json()
    assert payload["user_liked"] is True


def test_trivia_live_chat_rate_limit_fallback(client, test_db, current_user):
    enabled_patch, pre_patch, post_patch, draw_patch, active_patch, now = (
        _patch_trivia_live_chat_window()
    )
    draw_date = now.astimezone(pytz.UTC).replace(tzinfo=None).date()
    test_db.add(
        TriviaLiveChatMessage(
            user_id=current_user.account_id,
            message="Existing",
            draw_date=draw_date,
            created_at=now.astimezone(pytz.UTC).replace(tzinfo=None),
        )
    )
    test_db.commit()

    with enabled_patch, pre_patch, post_patch, draw_patch, active_patch, patch(
        "routers.trivia.trivia_live_chat.check_burst_limit",
        new=AsyncMock(return_value=True),
    ), patch(
        "routers.trivia.trivia_live_chat.check_rate_limit",
        new=AsyncMock(return_value=None),
    ), patch(
        "routers.trivia.trivia_live_chat.TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_MINUTE", 1
    ):
        response = client.post("/trivia-live-chat/send", json={"message": "Hello"})

    assert response.status_code == 429


def test_trivia_live_chat_push_respects_mutes(test_db):
    user1 = test_db.query(User).first()
    user2 = test_db.query(User).filter(User.account_id != user1.account_id).first()
    test_db.add(
        ChatMutePreferences(
            user_id=user2.account_id,
            global_chat_muted=False,
            trivia_live_chat_muted=True,
        )
    )
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
            player_id="player-muted",
            platform="ios",
            is_valid=True,
            last_active=datetime.utcnow() - timedelta(minutes=10),
        )
    )
    test_db.commit()

    async_mock = AsyncMock(return_value=True)

    def _override_get_db():
        yield test_db

    with patch.object(db_module, "get_db", _override_get_db), patch(
        "routers.trivia.trivia_live_chat.send_push_notification_async", async_mock
    ), patch("utils.notification_storage.create_notifications_batch"):
        send_push_for_trivia_live_chat_sync(
            message_id=1,
            sender_id=user1.account_id,
            sender_username="user1",
            message="Hello",
            draw_date=datetime.utcnow().date(),
            created_at=datetime.utcnow(),
        )

    all_player_ids = [call.kwargs["player_ids"] for call in async_mock.call_args_list]
    flattened = [pid for batch in all_player_ids for pid in batch]
    assert "player-muted" not in flattened
