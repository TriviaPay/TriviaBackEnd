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


_define_group_models()

groups_router = importlib.import_module("routers.groups")
groups_router = importlib.reload(groups_router)

Group = models.Group
GroupParticipant = models.GroupParticipant


def _make_client(test_db, user, monkeypatch):
    app = FastAPI()
    app.include_router(groups_router.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(groups_router, "GROUPS_ENABLED", True)
    return TestClient(app)


def test_list_groups_returns_roles_and_counts(test_db, monkeypatch):
    user = test_db.query(User).first()
    other = test_db.query(User).filter(User.account_id != user.account_id).first()

    group1 = Group(
        id=uuid.uuid4(),
        title="Group 1",
        created_by=user.account_id,
        updated_at=datetime.utcnow() - timedelta(hours=2),
    )
    group2 = Group(
        id=uuid.uuid4(),
        title="Group 2",
        created_by=user.account_id,
        updated_at=datetime.utcnow(),
    )
    test_db.add_all([group1, group2])
    test_db.add_all([
        GroupParticipant(group_id=group1.id, user_id=user.account_id, role="owner", is_banned=False),
        GroupParticipant(group_id=group2.id, user_id=user.account_id, role="member", is_banned=False),
        GroupParticipant(group_id=group2.id, user_id=other.account_id, role="member", is_banned=False),
        GroupParticipant(group_id=group2.id, user_id=999, role="member", is_banned=True),
    ])
    test_db.commit()

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.get("/groups")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["groups"][0]["id"] == str(group2.id)
    assert payload["groups"][0]["participant_count"] == 2
    assert payload["groups"][0]["my_role"] == "member"
    assert payload["groups"][1]["participant_count"] == 1
    assert payload["groups"][1]["my_role"] == "owner"


def test_get_group_requires_membership(test_db, monkeypatch):
    user = test_db.query(User).first()
    other = test_db.query(User).filter(User.account_id != user.account_id).first()

    group = Group(
        id=uuid.uuid4(),
        title="Group",
        created_by=user.account_id,
        updated_at=datetime.utcnow(),
    )
    test_db.add(group)
    test_db.add(GroupParticipant(group_id=group.id, user_id=user.account_id, role="owner", is_banned=False))
    test_db.commit()

    with _make_client(test_db, other, monkeypatch) as client:
        response = client.get(f"/groups/{group.id}")

    assert response.status_code == 403


def test_update_group_closed(test_db, monkeypatch):
    user = test_db.query(User).first()
    group = Group(
        id=uuid.uuid4(),
        title="Group",
        created_by=user.account_id,
        updated_at=datetime.utcnow(),
        is_closed=True,
    )
    test_db.add(group)
    test_db.add(GroupParticipant(group_id=group.id, user_id=user.account_id, role="owner", is_banned=False))
    test_db.commit()

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.patch(f"/groups/{group.id}", json={"title": "New"})

    assert response.status_code == 409
    assert response.json()["detail"] == "Group is closed"


def test_delete_group_already_closed(test_db, monkeypatch):
    user = test_db.query(User).first()
    group = Group(
        id=uuid.uuid4(),
        title="Group",
        created_by=user.account_id,
        updated_at=datetime.utcnow(),
        is_closed=True,
    )
    test_db.add(group)
    test_db.add(GroupParticipant(group_id=group.id, user_id=user.account_id, role="owner", is_banned=False))
    test_db.commit()

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.delete(f"/groups/{group.id}")

    assert response.status_code == 200
    assert response.json()["message"] == "Group already closed"
