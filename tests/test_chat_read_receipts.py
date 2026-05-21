from datetime import datetime, timedelta

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import config
import routers.messaging.service as messaging_service
import utils.chat_helpers as chat_helpers
import utils.chat_redis as chat_redis
from models import (
    GlobalChatMessage,
    GlobalChatViewer,
    PrivateChatConversation,
    PrivateChatMessage,
    User,
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)
    tables = [
        User.__table__,
        GlobalChatMessage.__table__,
        GlobalChatViewer.__table__,
        PrivateChatConversation.__table__,
        PrivateChatMessage.__table__,
    ]
    for table in tables:
        table.create(bind=engine)

    db = SessionLocal()
    db.add_all(
        [
            User(
                descope_user_id="test_user_1",
                email="test1@example.com",
                username="testuser1",
            ),
            User(
                descope_user_id="test_user_2",
                email="test2@example.com",
                username="testuser2",
            ),
        ]
    )
    db.commit()

    try:
        yield db
    finally:
        db.close()
        for table in reversed(tables):
            table.drop(bind=engine)
        engine.dispose()


def _profile_map(*users):
    return {
        user.account_id: {
            "profile_pic_url": None,
            "avatar_url": None,
            "frame_url": None,
            "badge": None,
            "subscription_badges": [],
            "level": 1,
            "level_progress": "0/100",
        }
        for user in users
    }


@pytest.mark.asyncio
async def test_private_chat_sender_sees_read_receipt(db_session, monkeypatch):
    user1, user2 = db_session.query(User).order_by(User.account_id).all()
    conversation = PrivateChatConversation(
        user1_id=user1.account_id,
        user2_id=user2.account_id,
        status="accepted",
        requested_by=user1.account_id,
    )
    db_session.add(conversation)
    db_session.flush()

    message = PrivateChatMessage(
        conversation_id=conversation.id,
        sender_id=user1.account_id,
        message="hello",
        status="sent",
        created_at=datetime.utcnow(),
    )
    db_session.add(message)
    db_session.commit()

    monkeypatch.setattr(messaging_service, "PRIVATE_CHAT_ENABLED", True)
    monkeypatch.setattr(
        messaging_service,
        "_get_user_presence_info",
        lambda db, **kwargs: (False, None),
    )
    monkeypatch.setattr(
        messaging_service,
        "_batch_get_user_profile_data",
        lambda users, db: _profile_map(*users),
    )

    await messaging_service.mark_conversation_read(
        db_session,
        current_user=user2,
        conversation_id=conversation.id,
        message_id=message.id,
        background_tasks=BackgroundTasks(),
    )

    db_session.refresh(conversation)
    db_session.refresh(message)
    assert conversation.last_read_message_id_user2 == message.id
    assert message.status == "seen"
    assert message.delivered_at is not None

    payload = await messaging_service.get_private_messages(
        db_session,
        current_user=user1,
        conversation_id=conversation.id,
        limit=50,
    )

    returned = payload["messages"][0]
    assert returned["id"] == message.id
    assert returned["status"] == "seen"
    assert returned["is_read"] is True


@pytest.mark.asyncio
async def test_global_chat_messages_include_per_viewer_is_read(
    db_session, monkeypatch
):
    user1, user2 = db_session.query(User).order_by(User.account_id).all()
    base_time = datetime.utcnow() - timedelta(minutes=1)

    older_message = GlobalChatMessage(
        user_id=user2.account_id,
        message="older",
        created_at=base_time,
    )
    newer_message = GlobalChatMessage(
        user_id=user2.account_id,
        message="newer",
        created_at=base_time + timedelta(seconds=30),
    )
    db_session.add_all([older_message, newer_message])
    db_session.flush()
    db_session.add(
        GlobalChatViewer(
            user_id=user1.account_id,
            last_seen=base_time + timedelta(seconds=10),
        )
    )
    db_session.commit()

    async def fake_get_chat_redis():
        return None

    monkeypatch.setattr(config, "GLOBAL_CHAT_ENABLED", True)
    monkeypatch.setattr(chat_redis, "get_chat_redis", fake_get_chat_redis)
    monkeypatch.setattr(
        chat_helpers,
        "get_user_chat_profile_data_bulk",
        lambda users, db: _profile_map(*users),
    )
    monkeypatch.setattr(
        messaging_service.messaging_repository,
        "count_total_unread_private_messages",
        lambda db, user_id: 0,
    )
    monkeypatch.setattr(
        messaging_service.messaging_repository,
        "count_pending_private_chat_requests",
        lambda db, user_id: 0,
    )

    payload = await messaging_service.get_global_chat_messages(
        db_session, current_user=user1, limit=50, before=None
    )

    messages_by_id = {msg["id"]: msg for msg in payload["messages"]}
    assert messages_by_id[older_message.id]["is_read"] is True
    assert messages_by_id[newer_message.id]["is_read"] is False

    viewer = (
        db_session.query(GlobalChatViewer)
        .filter(GlobalChatViewer.user_id == user1.account_id)
        .first()
    )
    assert viewer is not None
    assert viewer.last_seen >= newer_message.created_at
