import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID

import models
from core.db import get_db
from models import Base, User
from routers.dependencies import get_current_user


def _define_group_models():
    if not hasattr(models, "E2EEDevice"):

        class E2EEDevice(Base):
            __tablename__ = "e2ee_devices"

            device_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
            user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
            device_name = Column(String, nullable=False, default="device")
            status = Column(String, nullable=False, default="active")
            created_at = Column(DateTime, default=datetime.utcnow)

        models.E2EEDevice = E2EEDevice

    if not hasattr(models, "Group"):

        class Group(Base):
            __tablename__ = "groups"

            id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
            title = Column(String, nullable=False)
            created_by = Column(
                BigInteger, ForeignKey("users.account_id"), nullable=False
            )
            created_at = Column(DateTime, default=datetime.utcnow)
            updated_at = Column(DateTime, default=datetime.utcnow)
            max_participants = Column(Integer, default=100, nullable=False)
            group_epoch = Column(Integer, default=0, nullable=False)
            is_closed = Column(Boolean, default=False, nullable=False)
            participant_count = Column(Integer, default=0, nullable=False)

        models.Group = Group

    if not hasattr(models, "GroupParticipant"):

        class GroupParticipant(Base):
            __tablename__ = "group_participants"

            id = Column(Integer, primary_key=True)
            group_id = Column(
                UUID(as_uuid=True), ForeignKey("groups.id"), nullable=False
            )
            user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
            role = Column(String, nullable=False, default="member")
            joined_at = Column(DateTime, default=datetime.utcnow)
            mute_until = Column(DateTime, nullable=True)
            is_banned = Column(Boolean, default=False, nullable=False)

        models.GroupParticipant = GroupParticipant

    if not hasattr(models, "GroupBan"):

        class GroupBan(Base):
            __tablename__ = "group_bans"

            group_id = Column(UUID(as_uuid=True), primary_key=True)
            user_id = Column(BigInteger, primary_key=True)
            banned_by = Column(BigInteger, nullable=True)
            reason = Column(String, nullable=True)
            banned_at = Column(DateTime, default=datetime.utcnow)

        models.GroupBan = GroupBan


_define_group_models()

from routers.messaging import group_members as group_members_router

Group = models.Group
GroupParticipant = models.GroupParticipant
GroupBan = models.GroupBan


@pytest.fixture
def current_user(test_db):
    return test_db.query(User).first()


@pytest.fixture
def peer_user(test_db, current_user):
    return (
        test_db.query(User).filter(User.account_id != current_user.account_id).first()
    )


def _make_client(test_db, user, monkeypatch):
    app = FastAPI()
    app.include_router(group_members_router.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(group_members_router, "GROUPS_ENABLED", True)
    monkeypatch.setattr(group_members_router, "GROUP_MAX_PARTICIPANTS", 5)
    monkeypatch.setattr(
        group_members_router, "publish_group_message", lambda *args, **kwargs: None
    )

    return app


def _create_group(test_db, owner_id, max_participants=5):
    group = Group(
        title="Test Group",
        created_by=owner_id,
        max_participants=max_participants,
        participant_count=1,
    )
    test_db.add(group)
    test_db.flush()
    participant = GroupParticipant(group_id=group.id, user_id=owner_id, role="owner")
    test_db.add(participant)
    test_db.commit()
    test_db.refresh(group)
    return group


def test_list_add_and_remove_members(test_db, current_user, peer_user, monkeypatch):
    app = _make_client(test_db, current_user, monkeypatch)
    group = _create_group(test_db, current_user.account_id)

    with TestClient(app) as client:
        response = client.get(f"/groups/{group.id}/members")
        assert response.status_code == 200
        assert len(response.json()["members"]) == 1

        response = client.post(
            f"/groups/{group.id}/members", json={"user_ids": [peer_user.account_id]}
        )
        assert response.status_code == 200
        assert response.json()["added_user_ids"] == [peer_user.account_id]

        response = client.delete(f"/groups/{group.id}/members/{peer_user.account_id}")
        assert response.status_code == 200

        response = client.delete(
            f"/groups/{group.id}/members/{current_user.account_id}"
        )
        assert response.status_code == 403


def test_add_member_respects_ban_and_capacity(
    test_db, current_user, peer_user, monkeypatch
):
    app = _make_client(test_db, current_user, monkeypatch)
    group = _create_group(test_db, current_user.account_id, max_participants=1)

    with TestClient(app) as client:
        response = client.post(
            f"/groups/{group.id}/members", json={"user_ids": [peer_user.account_id]}
        )
        assert response.status_code == 409

    group.max_participants = 5
    test_db.commit()
    test_db.add(
        GroupBan(
            group_id=group.id,
            user_id=peer_user.account_id,
            banned_by=current_user.account_id,
        )
    )
    test_db.commit()

    with TestClient(app) as client:
        response = client.post(
            f"/groups/{group.id}/members", json={"user_ids": [peer_user.account_id]}
        )
        assert response.status_code == 200
        assert response.json()["added_user_ids"] == []


def test_promote_demote_ban_unban_and_leave(
    test_db, current_user, peer_user, monkeypatch
):
    owner_app = _make_client(test_db, current_user, monkeypatch)
    member_app = _make_client(test_db, peer_user, monkeypatch)
    group = _create_group(test_db, current_user.account_id)

    test_db.add(
        GroupParticipant(group_id=group.id, user_id=peer_user.account_id, role="member")
    )
    group.participant_count = 2
    test_db.commit()

    with TestClient(owner_app) as client:
        response = client.post(
            f"/groups/{group.id}/promote", json={"user_id": peer_user.account_id}
        )
        assert response.status_code == 200

        response = client.post(
            f"/groups/{group.id}/demote", json={"user_id": peer_user.account_id}
        )
        assert response.status_code == 200

        response = client.post(
            f"/groups/{group.id}/ban",
            json={"user_id": peer_user.account_id, "reason": "spam"},
        )
        assert response.status_code == 200
        participant = (
            test_db.query(GroupParticipant)
            .filter(
                GroupParticipant.group_id == group.id,
                GroupParticipant.user_id == peer_user.account_id,
            )
            .first()
        )
        assert participant.is_banned is True

        response = client.delete(f"/groups/{group.id}/ban/{peer_user.account_id}")
        assert response.status_code == 200
        test_db.refresh(participant)
        assert participant.is_banned is False

    with TestClient(member_app) as client:
        response = client.post(f"/groups/{group.id}/leave")
        assert response.status_code == 200


def test_mute_group(test_db, current_user, monkeypatch):
    app = _make_client(test_db, current_user, monkeypatch)
    group = _create_group(test_db, current_user.account_id)

    with TestClient(app) as client:
        mute_until = (datetime.utcnow() + timedelta(hours=1)).isoformat()
        response = client.post(
            f"/groups/{group.id}/mute", json={"mute_until": mute_until}
        )
        assert response.status_code == 200
        response = client.post(f"/groups/{group.id}/mute", json={"mute_until": None})
        assert response.status_code == 200
