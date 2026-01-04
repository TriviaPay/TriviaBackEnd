from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import get_db
from models import User, PrivateChatConversation, PrivateChatMessage, UserPresence, Block
from routers.dependencies import get_current_user
from routers import private_chat as private_chat_router


def _setup_app(test_db, user, monkeypatch):
    app = FastAPI()
    app.include_router(private_chat_router.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user

    monkeypatch.setattr(private_chat_router, "PRIVATE_CHAT_ENABLED", True)
    monkeypatch.setattr(private_chat_router, "PRESENCE_ENABLED", True)
    monkeypatch.setattr(private_chat_router, "check_burst_limit", AsyncMock(return_value=True))
    monkeypatch.setattr(private_chat_router, "check_rate_limit", AsyncMock(return_value=True))
    monkeypatch.setattr(private_chat_router, "enqueue_chat_event", AsyncMock(return_value=True))
    monkeypatch.setattr(private_chat_router, "should_emit_typing_event", AsyncMock(return_value=True))
    monkeypatch.setattr(private_chat_router, "clear_typing_event", AsyncMock(return_value=None))
    monkeypatch.setattr(private_chat_router, "publish_chat_message_sync", lambda *args, **kwargs: None)
    monkeypatch.setattr(private_chat_router, "send_push_if_needed_sync", lambda *args, **kwargs: None)

    return app


@pytest.fixture
def user1(test_db):
    return test_db.query(User).first()


@pytest.fixture
def user2(test_db, user1):
    return test_db.query(User).filter(User.account_id != user1.account_id).first()


def test_send_accept_and_list_conversations(test_db, user1, user2, monkeypatch):
    app_user1 = _setup_app(test_db, user1, monkeypatch)
    app_user2 = _setup_app(test_db, user2, monkeypatch)

    with TestClient(app_user1) as client_user1, TestClient(app_user2) as client_user2:
        response = client_user1.post("/private-chat/send", json={
            "recipient_id": user2.account_id,
            "message": "Hello",
            "client_message_id": "msg-1"
        })
        assert response.status_code == 200
        payload = response.json()
        conversation_id = payload["conversation_id"]

        response = client_user1.post("/private-chat/send", json={
            "recipient_id": user2.account_id,
            "message": "Second",
            "client_message_id": "msg-2"
        })
        assert response.status_code == 403

        response = client_user2.post("/private-chat/accept-reject", json={
            "conversation_id": conversation_id,
            "action": "accept"
        })
        assert response.status_code == 200
        assert response.json()["status"] == "accepted"

        response = client_user1.post("/private-chat/send", json={
            "recipient_id": user2.account_id,
            "message": "Accepted",
            "client_message_id": "msg-3"
        })
        assert response.status_code == 200

        response = client_user1.get("/private-chat/conversations")
        assert response.status_code == 200
        assert response.json()["conversations"]


def test_messages_mark_read_delivered_and_typing(test_db, user1, user2, monkeypatch):
    app_user1 = _setup_app(test_db, user1, monkeypatch)
    app_user2 = _setup_app(test_db, user2, monkeypatch)

    with TestClient(app_user1) as client_user1, TestClient(app_user2) as client_user2:
        response = client_user1.post("/private-chat/send", json={
            "recipient_id": user2.account_id,
            "message": "Ping",
            "client_message_id": "msg-4"
        })
        assert response.status_code == 200
        conversation_id = response.json()["conversation_id"]

        client_user2.post("/private-chat/accept-reject", json={
            "conversation_id": conversation_id,
            "action": "accept"
        })

        response = client_user1.post("/private-chat/send", json={
            "recipient_id": user2.account_id,
            "message": "Ping 2",
            "client_message_id": "msg-5"
        })
        assert response.status_code == 200
        message_id = response.json()["message_id"]

        response = client_user2.get(f"/private-chat/conversations/{conversation_id}/messages")
        assert response.status_code == 200
        assert response.json()["messages"]

        response = client_user2.post(f"/private-chat/messages/{message_id}/mark-delivered")
        assert response.status_code == 200

        response = client_user2.post(f"/private-chat/conversations/{conversation_id}/mark-read")
        assert response.status_code == 200

        response = client_user1.post(f"/private-chat/conversations/{conversation_id}/typing")
        assert response.status_code == 200
        response = client_user1.post(f"/private-chat/conversations/{conversation_id}/typing-stop")
        assert response.status_code == 200


def test_block_unblock_and_presence_creation(test_db, user1, user2, monkeypatch):
    app_user1 = _setup_app(test_db, user1, monkeypatch)
    with TestClient(app_user1) as client:
        response = client.post("/private-chat/block", json={"blocked_user_id": user2.account_id})
        assert response.status_code == 200
        assert test_db.query(Block).count() == 1

        response = client.get("/private-chat/blocks")
        assert response.status_code == 200
        assert response.json()["blocked_users"]

        response = client.delete(f"/private-chat/block/{user2.account_id}")
        assert response.status_code == 200
        assert test_db.query(Block).count() == 0

        conversation = PrivateChatConversation(
            user1_id=user1.account_id,
            user2_id=user2.account_id,
            status="accepted",
            requested_by=user1.account_id
        )
        test_db.add(conversation)
        test_db.flush()
        test_db.add(PrivateChatMessage(
            conversation_id=conversation.id,
            sender_id=user1.account_id,
            message="Presence",
            status="sent",
            created_at=datetime.utcnow()
        ))
        test_db.commit()

        response = client.get("/private-chat/conversations")
        assert response.status_code == 200
        assert response.json()["conversations"]
        assert test_db.query(UserPresence).filter(UserPresence.user_id == user2.account_id).count() == 1
