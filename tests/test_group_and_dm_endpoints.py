import base64
import importlib
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

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
    LargeBinary,
    String,
)
from sqlalchemy.dialects.postgresql import UUID

import models
from db import get_db
from models import Base, Block, User
from routers.dependencies import get_current_user


def _define_group_and_dm_models():
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
            mute_until = Column(DateTime, nullable=True)
            is_banned = Column(Boolean, default=False, nullable=False)

        models.GroupParticipant = GroupParticipant

    if not hasattr(models, "GroupMessage"):

        class GroupMessage(Base):
            __tablename__ = "group_messages"

            id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
            group_id = Column(
                UUID(as_uuid=True), ForeignKey("groups.id"), nullable=False
            )
            sender_user_id = Column(
                BigInteger, ForeignKey("users.account_id"), nullable=False
            )
            sender_device_id = Column(UUID(as_uuid=True), nullable=False)
            ciphertext = Column(LargeBinary, nullable=False)
            proto = Column(Integer, nullable=False)
            group_epoch = Column(Integer, nullable=False)
            created_at = Column(DateTime, default=datetime.utcnow)
            client_message_id = Column(String, nullable=True)
            reply_to_message_id = Column(UUID(as_uuid=True), nullable=True)

        models.GroupMessage = GroupMessage

    if not hasattr(models, "GroupDelivery"):

        class GroupDelivery(Base):
            __tablename__ = "group_delivery"

            id = Column(Integer, primary_key=True)
            message_id = Column(
                UUID(as_uuid=True), ForeignKey("group_messages.id"), nullable=False
            )
            recipient_user_id = Column(
                BigInteger, ForeignKey("users.account_id"), nullable=False
            )
            delivered_at = Column(DateTime, nullable=True)
            read_at = Column(DateTime, nullable=True)

        models.GroupDelivery = GroupDelivery

    if not hasattr(models, "GroupBan"):

        class GroupBan(Base):
            __tablename__ = "group_bans"

            group_id = Column(UUID(as_uuid=True), primary_key=True)
            user_id = Column(BigInteger, primary_key=True)
            banned_by = Column(BigInteger, nullable=True)
            reason = Column(String, nullable=True)
            banned_at = Column(DateTime, default=datetime.utcnow)

        models.GroupBan = GroupBan

    if not hasattr(models, "DMConversation"):

        class DMConversation(Base):
            __tablename__ = "dm_conversations"

            id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
            last_message_at = Column(DateTime, nullable=True)
            created_at = Column(DateTime, default=datetime.utcnow)

        models.DMConversation = DMConversation

    if not hasattr(models, "DMParticipant"):

        class DMParticipant(Base):
            __tablename__ = "dm_participants"

            id = Column(Integer, primary_key=True)
            conversation_id = Column(
                UUID(as_uuid=True), ForeignKey("dm_conversations.id"), nullable=False
            )
            user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)

        models.DMParticipant = DMParticipant

    if not hasattr(models, "DMMessage"):

        class DMMessage(Base):
            __tablename__ = "dm_messages"

            id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
            conversation_id = Column(
                UUID(as_uuid=True), ForeignKey("dm_conversations.id"), nullable=False
            )
            sender_user_id = Column(
                BigInteger, ForeignKey("users.account_id"), nullable=False
            )
            sender_device_id = Column(UUID(as_uuid=True), nullable=False)
            ciphertext = Column(LargeBinary, nullable=False)
            proto = Column(Integer, nullable=False)
            created_at = Column(DateTime, default=datetime.utcnow)
            client_message_id = Column(String, nullable=True)

        models.DMMessage = DMMessage

    if not hasattr(models, "DMDelivery"):

        class DMDelivery(Base):
            __tablename__ = "dm_delivery"

            id = Column(Integer, primary_key=True)
            message_id = Column(
                UUID(as_uuid=True), ForeignKey("dm_messages.id"), nullable=False
            )
            recipient_user_id = Column(
                BigInteger, ForeignKey("users.account_id"), nullable=False
            )
            delivered_at = Column(DateTime, nullable=True)
            read_at = Column(DateTime, nullable=True)

        models.DMDelivery = DMDelivery


_define_group_and_dm_models()

group_messages = importlib.import_module("routers.messaging.group_messages")
dm_messages = importlib.import_module("routers.messaging.dm_messages")

Group = models.Group
GroupParticipant = models.GroupParticipant
GroupMessage = models.GroupMessage
GroupDelivery = models.GroupDelivery
E2EEDevice = models.E2EEDevice
DMConversation = models.DMConversation
DMParticipant = models.DMParticipant
DMMessage = models.DMMessage
DMDelivery = models.DMDelivery


@pytest.fixture
def users(test_db):
    first = test_db.query(User).first()
    second = test_db.query(User).filter(User.account_id != first.account_id).first()
    return first, second


@pytest.fixture
def group_client(test_db, users, monkeypatch):
    user, _ = users
    app = FastAPI()
    app.include_router(group_messages.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user

    monkeypatch.setattr(group_messages, "GROUPS_ENABLED", True)
    monkeypatch.setattr(
        group_messages, "publish_group_message", lambda *args, **kwargs: None
    )

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides = {}


@pytest.fixture
def dm_client_user1(test_db, users, monkeypatch):
    user, _ = users
    app = FastAPI()
    app.include_router(dm_messages.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user

    monkeypatch.setattr(dm_messages, "E2EE_DM_ENABLED", True)
    monkeypatch.setattr(dm_messages, "publish_dm_message", AsyncMock())

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides = {}


@pytest.fixture
def dm_client_user2(test_db, users, monkeypatch):
    _, user = users
    app = FastAPI()
    app.include_router(dm_messages.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user

    monkeypatch.setattr(dm_messages, "E2EE_DM_ENABLED", True)
    monkeypatch.setattr(dm_messages, "publish_dm_message", AsyncMock())

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides = {}


@pytest.fixture
def group_client_user2(test_db, users, monkeypatch):
    _user1, user2 = users
    app = FastAPI()
    app.include_router(group_messages.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user2

    monkeypatch.setattr(group_messages, "GROUPS_ENABLED", True)
    monkeypatch.setattr(
        group_messages, "publish_group_message", lambda *args, **kwargs: None
    )

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides = {}


def _seed_group(test_db, users):
    owner, member = users
    group = Group(
        id=uuid.uuid4(),
        title="Test Group",
        created_by=owner.account_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        group_epoch=0,
        is_closed=False,
    )
    device = E2EEDevice(
        device_id=uuid.uuid4(),
        user_id=owner.account_id,
        device_name="device",
        status="active",
    )
    participants = [
        GroupParticipant(
            group_id=group.id,
            user_id=owner.account_id,
            role="owner",
            is_banned=False,
        ),
        GroupParticipant(
            group_id=group.id,
            user_id=member.account_id,
            role="member",
            is_banned=False,
        ),
    ]
    test_db.add(group)
    test_db.add(device)
    test_db.add_all(participants)
    test_db.commit()
    return group, device


def _seed_group_owner_only(test_db, users):
    owner, _member = users
    group = Group(
        id=uuid.uuid4(),
        title="Test Group",
        created_by=owner.account_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        group_epoch=0,
        is_closed=False,
    )
    device = E2EEDevice(
        device_id=uuid.uuid4(),
        user_id=owner.account_id,
        device_name="device",
        status="active",
    )
    participant = GroupParticipant(
        group_id=group.id,
        user_id=owner.account_id,
        role="owner",
        is_banned=False,
    )
    test_db.add(group)
    test_db.add(device)
    test_db.add(participant)
    test_db.commit()
    return group, device


def test_group_send_message_creates_deliveries(group_client, test_db, users):
    group, _device = _seed_group(test_db, users)
    payload = {
        "client_message_id": "group_msg_1",
        "ciphertext": base64.b64encode(b"hello").decode("utf-8"),
        "proto": 10,
        "group_epoch": 0,
    }

    response = group_client.post(f"/groups/{group.id}/messages", json=payload)

    assert response.status_code == 200
    assert test_db.query(GroupMessage).count() == 1
    deliveries = test_db.query(GroupDelivery).all()
    assert len(deliveries) == 1


def test_group_get_messages_includes_reply_id(group_client, test_db, users):
    group, device = _seed_group(test_db, users)
    base_message = GroupMessage(
        id=uuid.uuid4(),
        group_id=group.id,
        sender_user_id=users[0].account_id,
        sender_device_id=device.device_id,
        ciphertext=b"base",
        proto=10,
        group_epoch=0,
        created_at=datetime.utcnow(),
    )
    reply_message = GroupMessage(
        id=uuid.uuid4(),
        group_id=group.id,
        sender_user_id=users[0].account_id,
        sender_device_id=device.device_id,
        ciphertext=b"reply",
        proto=10,
        group_epoch=0,
        created_at=datetime.utcnow(),
        reply_to_message_id=base_message.id,
    )
    test_db.add_all([base_message, reply_message])
    test_db.commit()

    response = group_client.get(f"/groups/{group.id}/messages?limit=10")

    assert response.status_code == 200
    messages = response.json()["messages"]
    reply = next(msg for msg in messages if msg["id"] == str(reply_message.id))
    assert reply["reply_to_message_id"] == str(base_message.id)


def test_group_get_messages_requires_membership(group_client_user2, test_db, users):
    group, _device = _seed_group_owner_only(test_db, users)

    response = group_client_user2.get(f"/groups/{group.id}/messages?limit=10")

    assert response.status_code == 403


def test_group_send_epoch_stale(group_client, test_db, users):
    group, _device = _seed_group(test_db, users)
    group.group_epoch = 2
    test_db.commit()

    response = group_client.post(
        f"/groups/{group.id}/messages",
        json={
            "client_message_id": "group_msg_epoch",
            "ciphertext": base64.b64encode(b"hello").decode("utf-8"),
            "proto": 10,
            "group_epoch": 0,
        },
    )

    assert response.status_code == 409
    assert response.headers.get("X-Error-Code") == "EPOCH_STALE"
    assert response.headers.get("X-Current-Epoch") == "2"


def test_group_send_idempotent(group_client, test_db, users):
    group, _device = _seed_group(test_db, users)
    payload = {
        "client_message_id": "group_msg_dup",
        "ciphertext": base64.b64encode(b"hello").decode("utf-8"),
        "proto": 10,
        "group_epoch": 0,
    }

    response = group_client.post(f"/groups/{group.id}/messages", json=payload)
    assert response.status_code == 200
    message_id = response.json()["id"]

    response = group_client.post(f"/groups/{group.id}/messages", json=payload)
    assert response.status_code == 200
    assert response.json()["id"] == message_id


def test_group_send_device_revoked(group_client, test_db, users):
    group, _device = _seed_group_owner_only(test_db, users)
    device = (
        test_db.query(E2EEDevice)
        .filter(E2EEDevice.user_id == users[0].account_id)
        .first()
    )
    device.status = "revoked"
    test_db.commit()

    response = group_client.post(
        f"/groups/{group.id}/messages",
        json={
            "client_message_id": "group_msg_revoked",
            "ciphertext": base64.b64encode(b"hello").decode("utf-8"),
            "proto": 10,
            "group_epoch": 0,
        },
    )

    assert response.status_code == 409
    assert response.headers.get("X-Error-Code") == "DEVICE_REVOKED"


def test_group_rate_limit(group_client, test_db, users, monkeypatch):
    group, device = _seed_group(test_db, users)
    monkeypatch.setattr(group_messages, "GROUP_MESSAGE_RATE_PER_USER_PER_MIN", 1)

    test_db.add(
        GroupMessage(
            id=uuid.uuid4(),
            group_id=group.id,
            sender_user_id=users[0].account_id,
            sender_device_id=device.device_id,
            ciphertext=b"payload",
            proto=10,
            group_epoch=0,
            created_at=datetime.utcnow(),
        )
    )
    test_db.commit()

    response = group_client.post(
        f"/groups/{group.id}/messages",
        json={
            "client_message_id": "group_msg_rate",
            "ciphertext": base64.b64encode(b"hello").decode("utf-8"),
            "proto": 10,
            "group_epoch": 0,
        },
    )

    assert response.status_code == 429
    assert response.headers.get("X-RateLimit-Limit") == "1"
    assert response.headers.get("X-Retry-After") is not None


def test_group_burst_limit(group_client, test_db, users, monkeypatch):
    group, device = _seed_group(test_db, users)
    monkeypatch.setattr(group_messages, "GROUP_MESSAGE_RATE_PER_USER_PER_MIN", 100)
    monkeypatch.setattr(group_messages, "GROUP_BURST_PER_5S", 1)
    monkeypatch.setattr(group_messages, "GROUP_BURST_WINDOW_SECONDS", 5)

    test_db.add(
        GroupMessage(
            id=uuid.uuid4(),
            group_id=group.id,
            sender_user_id=users[0].account_id,
            sender_device_id=device.device_id,
            ciphertext=b"payload",
            proto=10,
            group_epoch=0,
            created_at=datetime.utcnow(),
        )
    )
    test_db.commit()

    response = group_client.post(
        f"/groups/{group.id}/messages",
        json={
            "client_message_id": "group_msg_burst",
            "ciphertext": base64.b64encode(b"hello").decode("utf-8"),
            "proto": 10,
            "group_epoch": 0,
        },
    )

    assert response.status_code == 429
    assert response.headers.get("X-RateLimit-Limit") == "1"
    assert response.headers.get("X-Retry-After") is not None


def test_group_reply_not_found(group_client, test_db, users):
    group, _device = _seed_group(test_db, users)
    response = group_client.post(
        f"/groups/{group.id}/messages",
        json={
            "client_message_id": "group_msg_reply",
            "ciphertext": base64.b64encode(b"hello").decode("utf-8"),
            "proto": 10,
            "group_epoch": 0,
            "reply_to_message_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 404


def test_group_mark_delivered_idempotent(group_client_user2, test_db, users):
    group, device = _seed_group(test_db, users)
    message = GroupMessage(
        id=uuid.uuid4(),
        group_id=group.id,
        sender_user_id=users[0].account_id,
        sender_device_id=device.device_id,
        ciphertext=b"payload",
        proto=10,
        group_epoch=0,
        created_at=datetime.utcnow(),
    )
    test_db.add(message)
    test_db.commit()

    response = group_client_user2.post(f"/groups/group-messages/{message.id}/delivered")
    assert response.status_code == 200
    response = group_client_user2.post(f"/groups/group-messages/{message.id}/delivered")
    assert response.status_code == 200


def test_group_mark_read_idempotent(group_client_user2, test_db, users):
    group, device = _seed_group(test_db, users)
    message = GroupMessage(
        id=uuid.uuid4(),
        group_id=group.id,
        sender_user_id=users[0].account_id,
        sender_device_id=device.device_id,
        ciphertext=b"payload",
        proto=10,
        group_epoch=0,
        created_at=datetime.utcnow(),
    )
    test_db.add(message)
    test_db.commit()

    response = group_client_user2.post(f"/groups/group-messages/{message.id}/read")
    assert response.status_code == 200
    response = group_client_user2.post(f"/groups/group-messages/{message.id}/read")
    assert response.status_code == 200


def test_group_delete_forbidden(group_client_user2, test_db, users):
    group, device = _seed_group(test_db, users)
    message = GroupMessage(
        id=uuid.uuid4(),
        group_id=group.id,
        sender_user_id=users[0].account_id,
        sender_device_id=device.device_id,
        ciphertext=b"payload",
        proto=10,
        group_epoch=0,
        created_at=datetime.utcnow(),
    )
    test_db.add(message)
    test_db.commit()

    response = group_client_user2.delete(f"/groups/group-messages/{message.id}")

    assert response.status_code == 403


def _seed_dm_conversation(test_db, users):
    sender, recipient = users
    conversation = DMConversation(id=uuid.uuid4(), created_at=datetime.utcnow())
    participants = [
        DMParticipant(conversation_id=conversation.id, user_id=sender.account_id),
        DMParticipant(conversation_id=conversation.id, user_id=recipient.account_id),
    ]
    device = E2EEDevice(
        device_id=uuid.uuid4(),
        user_id=sender.account_id,
        device_name="device",
        status="active",
    )
    test_db.add(conversation)
    test_db.add_all(participants)
    test_db.add(device)
    test_db.commit()
    return conversation, device


def _seed_dm_conversation_no_device(test_db, users):
    sender, recipient = users
    conversation = DMConversation(id=uuid.uuid4(), created_at=datetime.utcnow())
    participants = [
        DMParticipant(conversation_id=conversation.id, user_id=sender.account_id),
        DMParticipant(conversation_id=conversation.id, user_id=recipient.account_id),
    ]
    test_db.add(conversation)
    test_db.add_all(participants)
    test_db.commit()
    return conversation


def test_dm_send_message_creates_delivery(dm_client_user1, test_db, users):
    conversation, _device = _seed_dm_conversation(test_db, users)
    payload = {
        "client_message_id": "dm_msg_1",
        "ciphertext": base64.b64encode(b"hello").decode("utf-8"),
        "proto": 1,
    }

    response = dm_client_user1.post(
        f"/dm/conversations/{conversation.id}/messages",
        json=payload,
    )

    assert response.status_code == 200
    assert test_db.query(DMMessage).count() == 1
    deliveries = test_db.query(DMDelivery).all()
    assert len(deliveries) == 1
    assert deliveries[0].recipient_user_id == users[1].account_id


def test_dm_get_messages_returns_ciphertext(dm_client_user1, test_db, users):
    conversation, device = _seed_dm_conversation(test_db, users)
    message = DMMessage(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        sender_user_id=users[0].account_id,
        sender_device_id=device.device_id,
        ciphertext=b"payload",
        proto=1,
        created_at=datetime.utcnow(),
        client_message_id="dm_msg_2",
    )
    test_db.add(message)
    test_db.commit()

    response = dm_client_user1.get(
        f"/dm/conversations/{conversation.id}/messages?limit=10"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["messages"][0]["id"] == str(message.id)
    assert payload["messages"][0]["ciphertext"] == base64.b64encode(b"payload").decode(
        "utf-8"
    )


def test_dm_send_disabled(dm_client_user1, test_db, users, monkeypatch):
    conversation, _device = _seed_dm_conversation(test_db, users)
    monkeypatch.setattr(dm_messages, "E2EE_DM_ENABLED", False)

    response = dm_client_user1.post(
        f"/dm/conversations/{conversation.id}/messages",
        json={"ciphertext": base64.b64encode(b"hello").decode("utf-8"), "proto": 1},
    )

    assert response.status_code == 403


def test_dm_send_no_active_device(dm_client_user1, test_db, users):
    conversation = _seed_dm_conversation_no_device(test_db, users)

    response = dm_client_user1.post(
        f"/dm/conversations/{conversation.id}/messages",
        json={"ciphertext": base64.b64encode(b"hello").decode("utf-8"), "proto": 1},
    )

    assert response.status_code == 400


def test_dm_send_device_revoked(dm_client_user1, test_db, users):
    conversation, device = _seed_dm_conversation(test_db, users)
    device.status = "revoked"
    test_db.commit()

    response = dm_client_user1.post(
        f"/dm/conversations/{conversation.id}/messages",
        json={"ciphertext": base64.b64encode(b"hello").decode("utf-8"), "proto": 1},
    )

    assert response.status_code == 409
    assert response.headers.get("X-Error-Code") == "DEVICE_REVOKED"


def test_dm_send_blocked(dm_client_user1, test_db, users):
    conversation, _device = _seed_dm_conversation(test_db, users)
    test_db.add(
        Block(
            blocker_id=users[1].account_id,
            blocked_id=users[0].account_id,
        )
    )
    test_db.commit()

    response = dm_client_user1.post(
        f"/dm/conversations/{conversation.id}/messages",
        json={"ciphertext": base64.b64encode(b"hello").decode("utf-8"), "proto": 1},
    )

    assert response.status_code == 403
    assert response.headers.get("X-Error-Code") == "BLOCKED"


def test_dm_send_idempotent(dm_client_user1, test_db, users):
    conversation, _device = _seed_dm_conversation(test_db, users)
    payload = {
        "client_message_id": "dm_msg_dup",
        "ciphertext": base64.b64encode(b"hello").decode("utf-8"),
        "proto": 1,
    }

    response = dm_client_user1.post(
        f"/dm/conversations/{conversation.id}/messages",
        json=payload,
    )
    assert response.status_code == 200
    message_id = response.json()["message_id"]

    response = dm_client_user1.post(
        f"/dm/conversations/{conversation.id}/messages",
        json=payload,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["duplicate"] is True
    assert payload["message_id"] == message_id


def test_dm_rate_limit(dm_client_user1, test_db, users, monkeypatch):
    conversation, device = _seed_dm_conversation(test_db, users)
    monkeypatch.setattr(dm_messages, "E2EE_DM_MAX_MESSAGES_PER_MINUTE", 1)

    test_db.add(
        DMMessage(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            sender_user_id=users[0].account_id,
            sender_device_id=device.device_id,
            ciphertext=b"payload",
            proto=1,
            created_at=datetime.utcnow(),
        )
    )
    test_db.commit()

    response = dm_client_user1.post(
        f"/dm/conversations/{conversation.id}/messages",
        json={"ciphertext": base64.b64encode(b"hello").decode("utf-8"), "proto": 1},
    )

    assert response.status_code == 429
    assert response.headers.get("X-RateLimit-Limit") == "1"
    assert response.headers.get("X-Retry-After") is not None


def test_dm_burst_limit(dm_client_user1, test_db, users, monkeypatch):
    conversation, device = _seed_dm_conversation(test_db, users)
    monkeypatch.setattr(dm_messages, "E2EE_DM_MAX_MESSAGES_PER_CONVERSATION_BURST", 1)
    monkeypatch.setattr(dm_messages, "E2EE_DM_BURST_WINDOW_SECONDS", 60)

    test_db.add(
        DMMessage(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            sender_user_id=users[0].account_id,
            sender_device_id=device.device_id,
            ciphertext=b"payload",
            proto=1,
            created_at=datetime.utcnow(),
        )
    )
    test_db.commit()

    response = dm_client_user1.post(
        f"/dm/conversations/{conversation.id}/messages",
        json={"ciphertext": base64.b64encode(b"hello").decode("utf-8"), "proto": 1},
    )

    assert response.status_code == 429
    assert response.headers.get("X-RateLimit-Limit") == "1"
    assert response.headers.get("X-Retry-After") is not None


def test_dm_since_pagination(dm_client_user1, test_db, users):
    conversation, device = _seed_dm_conversation(test_db, users)
    base_time = datetime.utcnow()
    first = DMMessage(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        sender_user_id=users[0].account_id,
        sender_device_id=device.device_id,
        ciphertext=b"first",
        proto=1,
        created_at=base_time,
    )
    second = DMMessage(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        sender_user_id=users[0].account_id,
        sender_device_id=device.device_id,
        ciphertext=b"second",
        proto=1,
        created_at=base_time + timedelta(seconds=1),
    )
    test_db.add_all([first, second])
    test_db.commit()

    response = dm_client_user1.get(
        f"/dm/conversations/{conversation.id}/messages?since={first.id}"
    )

    assert response.status_code == 200
    payload = response.json()
    ids = [msg["id"] for msg in payload["messages"]]
    assert str(first.id) not in ids


def test_dm_mark_delivered_and_read(dm_client_user2, test_db, users):
    conversation, device = _seed_dm_conversation(test_db, users)
    message = DMMessage(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        sender_user_id=users[0].account_id,
        sender_device_id=device.device_id,
        ciphertext=b"payload",
        proto=1,
        created_at=datetime.utcnow(),
        client_message_id="dm_msg_3",
    )
    test_db.add(message)
    test_db.commit()

    delivery = DMDelivery(
        message_id=message.id,
        recipient_user_id=users[1].account_id,
    )
    test_db.add(delivery)
    test_db.commit()

    response = dm_client_user2.post(f"/dm/messages/{message.id}/delivered")
    assert response.status_code == 200

    response = dm_client_user2.post(f"/dm/messages/{message.id}/read")
    assert response.status_code == 200

    test_db.refresh(delivery)
    assert delivery.delivered_at is not None
    assert delivery.read_at is not None
