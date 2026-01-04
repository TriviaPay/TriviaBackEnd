from datetime import datetime, timedelta
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    JSON,
)
from sqlalchemy.dialects.postgresql import UUID

import models
from db import get_db
from models import Base, User, UserPresence, Block
from routers.dependencies import get_current_user


def _define_status_models():
    if not hasattr(models, "DMConversation"):
        class DMConversation(Base):
            __tablename__ = "dm_conversations"

            id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
            created_at = Column(DateTime, default=datetime.utcnow)

        models.DMConversation = DMConversation

    if not hasattr(models, "DMParticipant"):
        class DMParticipant(Base):
            __tablename__ = "dm_participants"

            id = Column(Integer, primary_key=True)
            conversation_id = Column(UUID(as_uuid=True), ForeignKey("dm_conversations.id"), nullable=False)
            user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)

        models.DMParticipant = DMParticipant

    if not hasattr(models, "StatusPost"):
        class StatusPost(Base):
            __tablename__ = "status_posts"

            id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
            owner_user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
            created_at = Column(DateTime, default=datetime.utcnow)
            expires_at = Column(DateTime, nullable=True)
            media_meta = Column(JSON, nullable=True)
            audience_mode = Column(String, nullable=False, default="contacts")
            post_epoch = Column(Integer, nullable=False, default=0)

        models.StatusPost = StatusPost

    if not hasattr(models, "StatusAudience"):
        class StatusAudience(Base):
            __tablename__ = "status_audience"

            post_id = Column(UUID(as_uuid=True), ForeignKey("status_posts.id"), primary_key=True)
            viewer_user_id = Column(BigInteger, ForeignKey("users.account_id"), primary_key=True)

        models.StatusAudience = StatusAudience

    if not hasattr(models, "StatusView"):
        class StatusView(Base):
            __tablename__ = "status_views"

            post_id = Column(UUID(as_uuid=True), ForeignKey("status_posts.id"), primary_key=True)
            viewer_user_id = Column(BigInteger, ForeignKey("users.account_id"), primary_key=True)
            viewed_at = Column(DateTime, nullable=True)

        models.StatusView = StatusView


_define_status_models()

from routers import status as status_router

StatusPost = models.StatusPost
StatusAudience = models.StatusAudience
StatusView = models.StatusView
DMConversation = models.DMConversation
DMParticipant = models.DMParticipant


@pytest.fixture
def current_user(test_db):
    return test_db.query(User).first()


@pytest.fixture
def peer_user(test_db, current_user):
    return test_db.query(User).filter(User.account_id != current_user.account_id).first()


@pytest.fixture
def client(test_db, current_user, monkeypatch):
    app = FastAPI()
    app.include_router(status_router.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: current_user

    monkeypatch.setattr(status_router, "STATUS_ENABLED", True)
    monkeypatch.setattr(status_router, "STATUS_TTL_HOURS", 24)
    monkeypatch.setattr(status_router, "STATUS_MAX_POSTS_PER_DAY", 5)
    monkeypatch.setattr(status_router, "publish_dm_message", lambda *args, **kwargs: None)

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides = {}


def _create_contact(test_db, user_id, peer_id):
    conversation = DMConversation(id=uuid.uuid4(), created_at=datetime.utcnow())
    test_db.add(conversation)
    test_db.flush()
    test_db.add_all([
        DMParticipant(conversation_id=conversation.id, user_id=user_id),
        DMParticipant(conversation_id=conversation.id, user_id=peer_id),
    ])
    test_db.commit()


def test_create_status_post_contacts_and_custom(client, test_db, current_user, peer_user):
    _create_contact(test_db, current_user.account_id, peer_user.account_id)

    response = client.post("/status/posts", json={
        "media_meta": {"url": "https://example.com/a.jpg"},
        "audience_mode": "contacts"
    })
    assert response.status_code == 200
    assert response.json()["audience_count"] == 1

    response = client.post("/status/posts", json={
        "media_meta": {"url": "https://example.com/b.jpg"},
        "audience_mode": "custom",
        "custom_audience": [peer_user.account_id]
    })
    assert response.status_code == 200


def test_feed_mark_viewed_and_delete(client, test_db, current_user, peer_user):
    post = StatusPost(
        id=uuid.uuid4(),
        owner_user_id=peer_user.account_id,
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(hours=1),
        media_meta={"url": "https://example.com/feed.jpg"},
        audience_mode="custom",
        post_epoch=0
    )
    test_db.add(post)
    test_db.flush()
    test_db.add(StatusAudience(post_id=post.id, viewer_user_id=current_user.account_id))
    test_db.commit()

    response = client.get("/status/feed")
    assert response.status_code == 200
    payload = response.json()
    assert payload["posts"]
    assert payload["posts"][0]["id"] == str(post.id)

    response = client.post("/status/views", json={"post_ids": [str(post.id)]})
    assert response.status_code == 200
    assert response.json()["viewed_post_ids"] == [str(post.id)]

    response = client.post("/status/views", json={"post_ids": [str(post.id)]})
    assert response.status_code == 200

    my_post = StatusPost(
        id=uuid.uuid4(),
        owner_user_id=current_user.account_id,
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(hours=1),
        media_meta={"url": "https://example.com/own.jpg"},
        audience_mode="custom",
        post_epoch=0
    )
    test_db.add(my_post)
    test_db.flush()
    test_db.add(StatusAudience(post_id=my_post.id, viewer_user_id=current_user.account_id))
    test_db.commit()

    response = client.delete(f"/status/posts/{my_post.id}")
    assert response.status_code == 200
    assert test_db.query(StatusPost).filter(StatusPost.id == my_post.id).count() == 0


def test_presence_privacy_and_blocks(client, test_db, current_user, peer_user):
    _create_contact(test_db, current_user.account_id, peer_user.account_id)
    presence = UserPresence(
        user_id=peer_user.account_id,
        last_seen_at=datetime.utcnow(),
        device_online=True,
        privacy_settings={"share_last_seen": "contacts", "share_online": True}
    )
    test_db.add(presence)
    test_db.commit()

    response = client.get(f"/status/presence?user_ids={peer_user.account_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["presence"][0]["device_online"] is True
    assert payload["presence"][0]["last_seen_at"] is not None

    test_db.add(Block(blocker_id=current_user.account_id, blocked_id=peer_user.account_id))
    test_db.commit()
    response = client.get(f"/status/presence?user_ids={peer_user.account_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["presence"][0]["device_online"] is False
    assert payload["presence"][0]["last_seen_at"] is None


def test_presence_non_contact_hidden(client, test_db, current_user, peer_user):
    presence = UserPresence(
        user_id=peer_user.account_id,
        last_seen_at=datetime.utcnow(),
        device_online=True,
        privacy_settings={"share_last_seen": "contacts", "share_online": False}
    )
    test_db.add(presence)
    test_db.commit()

    response = client.get(f"/status/presence?user_ids={peer_user.account_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["presence"][0]["device_online"] is False
    assert payload["presence"][0]["last_seen_at"] is None
