import importlib
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID

import models
from db import get_db
from models import Base, User
from routers.dependencies import get_current_user


def _define_group_models():
    if not hasattr(models, "Group"):
        class Group(Base):
            __tablename__ = "groups"

            id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
            title = Column(String, nullable=False)
            about = Column(String, nullable=True)
            photo_url = Column(String, nullable=True)
            created_by = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
            created_at = Column(DateTime, default=datetime.utcnow)
            updated_at = Column(DateTime, default=datetime.utcnow)
            max_participants = Column(Integer, default=100, nullable=False)
            group_epoch = Column(Integer, default=0, nullable=False)
            is_closed = Column(Boolean, default=False, nullable=False)

        models.Group = Group

    if not hasattr(models, "GroupParticipant"):
        class GroupParticipant(Base):
            __tablename__ = "group_participants"

            id = Column(Integer, primary_key=True)
            group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id"), nullable=False)
            user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
            role = Column(String, nullable=False, default="member")
            joined_at = Column(DateTime, default=datetime.utcnow)
            is_banned = Column(Boolean, default=False, nullable=False)

        models.GroupParticipant = GroupParticipant

    if not hasattr(models, "GroupMessage"):
        class GroupMessage(Base):
            __tablename__ = "group_messages"

            id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
            group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id"), nullable=False)
            sender_user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
            created_at = Column(DateTime, default=datetime.utcnow)

        models.GroupMessage = GroupMessage

    if not hasattr(models, "GroupSenderKey"):
        class GroupSenderKey(Base):
            __tablename__ = "group_sender_keys"

            id = Column(Integer, primary_key=True)
            group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id"), nullable=False)
            created_at = Column(DateTime, default=datetime.utcnow)

        models.GroupSenderKey = GroupSenderKey


_define_group_models()

group_metrics = importlib.import_module("routers.group_metrics")
group_metrics = importlib.reload(group_metrics)

Group = models.Group
GroupParticipant = models.GroupParticipant
GroupMessage = models.GroupMessage
GroupSenderKey = models.GroupSenderKey


def _make_client(test_db, user, monkeypatch):
    app = FastAPI()
    app.include_router(group_metrics.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(group_metrics, "GROUPS_ENABLED", True)
    monkeypatch.setattr(group_metrics, "GROUP_METRICS_CACHE_SECONDS", 60)
    monkeypatch.setattr(group_metrics, "get_redis", lambda: None)
    group_metrics._metrics_cache["payload"] = None
    group_metrics._metrics_cache["ts"] = 0.0
    return TestClient(app)


def test_group_metrics_requires_admin(test_db, monkeypatch):
    user = test_db.query(User).first()
    user.is_admin = False
    test_db.commit()

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.get("/groups/metrics")

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required"


def test_group_metrics_payload_cached(test_db, monkeypatch):
    user = test_db.query(User).first()
    user.is_admin = True
    test_db.commit()

    group1 = Group(
        id=uuid.uuid4(),
        title="Group 1",
        created_by=user.account_id,
        updated_at=datetime.utcnow(),
        is_closed=False,
    )
    group2 = Group(
        id=uuid.uuid4(),
        title="Group 2",
        created_by=user.account_id,
        updated_at=datetime.utcnow() - timedelta(days=1),
        is_closed=True,
    )
    test_db.add_all([group1, group2])

    participants = [
        GroupParticipant(group_id=group1.id, user_id=user.account_id, role="owner", is_banned=False),
        GroupParticipant(group_id=group1.id, user_id=999, role="member", is_banned=False),
        GroupParticipant(group_id=group2.id, user_id=1000, role="member", is_banned=False),
        GroupParticipant(group_id=group2.id, user_id=1001, role="member", is_banned=False),
        GroupParticipant(group_id=group2.id, user_id=1002, role="member", is_banned=True),
    ]
    test_db.add_all(participants)

    now = datetime.utcnow()
    messages = [
        GroupMessage(group_id=group1.id, sender_user_id=user.account_id, created_at=now - timedelta(minutes=30)),
        GroupMessage(group_id=group1.id, sender_user_id=user.account_id, created_at=now - timedelta(minutes=10)),
        GroupMessage(group_id=group2.id, sender_user_id=user.account_id, created_at=now - timedelta(days=1)),
    ]
    test_db.add_all(messages)
    test_db.add(GroupSenderKey(group_id=group1.id))
    test_db.commit()

    client = _make_client(test_db, user, monkeypatch)
    response = client.get("/groups/metrics")

    assert response.status_code == 200
    payload = response.json()
    metrics = payload["metrics"]

    assert metrics["groups"]["total"] == 2
    assert metrics["groups"]["active"] == 1
    assert metrics["groups"]["closed"] == 1
    assert metrics["participants"]["average_per_group"] == 2.0
    assert metrics["messages"]["today"] == 2
    assert metrics["messages"]["last_hour"] == 2
    assert metrics["sender_keys"]["total_distributions"] == 1

    test_db.add(Group(id=uuid.uuid4(), title="Group 3", created_by=user.account_id))
    test_db.commit()

    cached_response = client.get("/groups/metrics")

    assert cached_response.status_code == 200
    assert cached_response.json() == payload
    client.close()
