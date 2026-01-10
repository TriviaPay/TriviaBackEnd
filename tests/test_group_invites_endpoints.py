import importlib
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
from db import get_db
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
            is_banned = Column(Boolean, default=False, nullable=False)

        models.GroupParticipant = GroupParticipant

    if not hasattr(models, "GroupInvite"):

        class GroupInvite(Base):
            __tablename__ = "group_invites"

            id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
            group_id = Column(
                UUID(as_uuid=True), ForeignKey("groups.id"), nullable=False
            )
            created_by = Column(
                BigInteger, ForeignKey("users.account_id"), nullable=False
            )
            type = Column(String, nullable=False)
            code = Column(String, nullable=False, unique=True, index=True)
            expires_at = Column(DateTime, nullable=True, index=True)
            max_uses = Column(Integer, nullable=True)
            uses = Column(Integer, default=0, nullable=False)
            created_at = Column(DateTime, default=datetime.utcnow)
            target_user_id = Column(BigInteger, nullable=True)

        models.GroupInvite = GroupInvite

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

group_invites = importlib.import_module("routers.messaging.group_invites")
group_invites = importlib.reload(group_invites)

Group = models.Group
GroupInvite = models.GroupInvite
GroupParticipant = models.GroupParticipant
GroupBan = models.GroupBan


@pytest.fixture
def users(test_db):
    first = test_db.query(User).first()
    second = test_db.query(User).filter(User.account_id != first.account_id).first()
    return first, second


def _make_client(test_db, user, monkeypatch):
    app = FastAPI()
    app.include_router(group_invites.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user

    monkeypatch.setattr(group_invites, "GROUPS_ENABLED", True)
    monkeypatch.setattr(group_invites, "check_group_role", lambda *args, **kwargs: None)

    def _increment_group_epoch(db, group):
        group.group_epoch += 1

    monkeypatch.setattr(group_invites, "increment_group_epoch", _increment_group_epoch)

    return TestClient(app)


def _create_group(test_db, owner_id):
    group = Group(
        title="Test group",
        created_by=owner_id,
        is_closed=False,
        group_epoch=0,
        max_participants=10,
    )
    test_db.add(group)
    test_db.commit()
    return group


def test_create_invite_requires_target_for_direct(test_db, users, monkeypatch):
    user, _ = users
    group = _create_group(test_db, user.account_id)

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.post(
            f"/groups/{group.id}/invites",
            json={"type": "direct", "expires_at": None},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "TARGET_USER_REQUIRED"


def test_create_invite_expiry_in_past(test_db, users, monkeypatch):
    user, _ = users
    group = _create_group(test_db, user.account_id)
    past = (datetime.utcnow() - timedelta(days=1)).isoformat()

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.post(
            f"/groups/{group.id}/invites",
            json={"type": "link", "expires_at": past},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "EXPIRY_IN_PAST"


def test_create_invite_retries_on_duplicate_code(test_db, users, monkeypatch):
    user, _ = users
    group = _create_group(test_db, user.account_id)
    existing = GroupInvite(
        group_id=group.id,
        created_by=user.account_id,
        type="link",
        code="DUPLICATE",
        expires_at=datetime.utcnow() + timedelta(hours=1),
        uses=0,
    )
    test_db.add(existing)
    test_db.commit()

    codes = iter(["DUPLICATE", "UNIQUECODE"])
    monkeypatch.setattr(group_invites, "generate_invite_code", lambda: next(codes))

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.post(
            f"/groups/{group.id}/invites",
            json={"type": "link"},
        )

    assert response.status_code == 200
    assert response.json()["code"] == "UNIQUECODE"


def test_list_invites_filters_expired_and_maxed(test_db, users, monkeypatch):
    user, _ = users
    group = _create_group(test_db, user.account_id)
    now = datetime.utcnow()

    test_db.add_all(
        [
            GroupInvite(
                group_id=group.id,
                created_by=user.account_id,
                type="link",
                code="ACTIVE",
                expires_at=now + timedelta(hours=1),
                max_uses=2,
                uses=1,
            ),
            GroupInvite(
                group_id=group.id,
                created_by=user.account_id,
                type="link",
                code="EXPIRED",
                expires_at=now - timedelta(hours=1),
                max_uses=2,
                uses=0,
            ),
            GroupInvite(
                group_id=group.id,
                created_by=user.account_id,
                type="link",
                code="MAXED",
                expires_at=now + timedelta(hours=1),
                max_uses=1,
                uses=1,
            ),
        ]
    )
    test_db.commit()

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.get(f"/groups/{group.id}/invites")

    assert response.status_code == 200
    codes = {invite["code"] for invite in response.json()["invites"]}
    assert codes == {"ACTIVE"}


def test_join_group_direct_target_and_success(test_db, users, monkeypatch):
    owner, target = users
    group = _create_group(test_db, owner.account_id)
    invite = GroupInvite(
        group_id=group.id,
        created_by=owner.account_id,
        type="direct",
        code="DIRECTCODE",
        expires_at=datetime.utcnow() + timedelta(hours=1),
        uses=0,
        target_user_id=target.account_id,
    )
    test_db.add(invite)
    test_db.commit()

    with _make_client(test_db, owner, monkeypatch) as client:
        response = client.post("/groups/join", json={"code": "DIRECTCODE"})
    assert response.status_code == 403
    assert response.json()["detail"] == "NOT_INVITED"

    with _make_client(test_db, target, monkeypatch) as client:
        response = client.post("/groups/join", json={"code": "DIRECTCODE"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["group_id"] == str(group.id)
    assert payload["new_epoch"] == 1

    invite = test_db.query(GroupInvite).filter(GroupInvite.code == "DIRECTCODE").first()
    assert invite.uses == 1


def test_join_group_banned(test_db, users, monkeypatch):
    user, _ = users
    group = _create_group(test_db, user.account_id)
    invite = GroupInvite(
        group_id=group.id,
        created_by=user.account_id,
        type="link",
        code="BANCODE",
        expires_at=datetime.utcnow() + timedelta(hours=1),
        uses=0,
    )
    test_db.add_all([invite, GroupBan(group_id=group.id, user_id=user.account_id)])
    test_db.commit()

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.post("/groups/join", json={"code": "BANCODE"})

    assert response.status_code == 403
    assert response.json()["detail"] == "BANNED"


def test_join_group_already_member(test_db, users, monkeypatch):
    user, _ = users
    group = _create_group(test_db, user.account_id)
    invite = GroupInvite(
        group_id=group.id,
        created_by=user.account_id,
        type="link",
        code="MEMBERCODE",
        expires_at=datetime.utcnow() + timedelta(hours=1),
        uses=0,
    )
    test_db.add_all(
        [
            invite,
            GroupParticipant(group_id=group.id, user_id=user.account_id, role="member"),
        ]
    )
    test_db.commit()

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.post("/groups/join", json={"code": "MEMBERCODE"})

    assert response.status_code == 200
    assert response.json()["message"] == "Already a member"


def test_join_group_expired_invite(test_db, users, monkeypatch):
    user, _ = users
    group = _create_group(test_db, user.account_id)
    invite = GroupInvite(
        group_id=group.id,
        created_by=user.account_id,
        type="link",
        code="EXPIREDCODE",
        expires_at=datetime.utcnow() - timedelta(minutes=1),
        uses=0,
    )
    test_db.add(invite)
    test_db.commit()

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.post("/groups/join", json={"code": "EXPIREDCODE"})

    assert response.status_code == 410


def test_join_group_max_uses(test_db, users, monkeypatch):
    user, _ = users
    group = _create_group(test_db, user.account_id)
    invite = GroupInvite(
        group_id=group.id,
        created_by=user.account_id,
        type="link",
        code="MAXEDCODE",
        expires_at=datetime.utcnow() + timedelta(hours=1),
        max_uses=1,
        uses=1,
    )
    test_db.add(invite)
    test_db.commit()

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.post("/groups/join", json={"code": "MAXEDCODE"})

    assert response.status_code == 409
    assert response.json()["detail"] == "MAX_USES"


def test_join_group_group_full(test_db, users, monkeypatch):
    owner, member = users
    monkeypatch.setattr(group_invites, "GROUP_MAX_PARTICIPANTS", 1)
    group = _create_group(test_db, owner.account_id)
    group.max_participants = 1
    test_db.add(
        GroupParticipant(group_id=group.id, user_id=owner.account_id, role="owner")
    )
    invite = GroupInvite(
        group_id=group.id,
        created_by=owner.account_id,
        type="link",
        code="FULLCODE",
        expires_at=datetime.utcnow() + timedelta(hours=1),
        uses=0,
    )
    test_db.add(invite)
    test_db.commit()

    with _make_client(test_db, member, monkeypatch) as client:
        response = client.post("/groups/join", json={"code": "FULLCODE"})

    assert response.status_code == 409
    assert response.json()["detail"] == "GROUP_FULL"


def test_join_group_closed(test_db, users, monkeypatch):
    user, _ = users
    group = _create_group(test_db, user.account_id)
    group.is_closed = True
    invite = GroupInvite(
        group_id=group.id,
        created_by=user.account_id,
        type="link",
        code="CLOSEDCODE",
        expires_at=datetime.utcnow() + timedelta(hours=1),
        uses=0,
    )
    test_db.add(invite)
    test_db.commit()

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.post("/groups/join", json={"code": "CLOSEDCODE"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Group is closed"
