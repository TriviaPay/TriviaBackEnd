import uuid
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

import models
from db import get_db
from models import Base, Block, User
from routers.dependencies import get_current_user


def _define_dm_models():
    if not hasattr(models, "E2EEDevice"):

        class E2EEDevice(Base):
            __tablename__ = "e2ee_devices"

            device_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
            user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
            device_name = Column(String, nullable=False, default="device")
            status = Column(String, nullable=False, default="active")
            created_at = Column(DateTime, default=datetime.utcnow)

        models.E2EEDevice = E2EEDevice

    if not hasattr(models, "DMConversation"):

        class DMConversation(Base):
            __tablename__ = "dm_conversations"

            id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
            last_message_at = Column(DateTime, nullable=True)
            created_at = Column(DateTime, default=datetime.utcnow)
            sealed_sender_enabled = Column(Boolean, default=False, nullable=False)
            pair_key = Column(String, unique=True, nullable=True)

        models.DMConversation = DMConversation

    if not hasattr(models, "DMParticipant"):

        class DMParticipant(Base):
            __tablename__ = "dm_participants"

            id = Column(Integer, primary_key=True)
            conversation_id = Column(
                UUID(as_uuid=True), ForeignKey("dm_conversations.id"), nullable=False
            )
            user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
            device_ids = Column(JSON, nullable=True)

            __table_args__ = (
                UniqueConstraint(
                    "conversation_id",
                    "user_id",
                    name="uq_dm_participants_conversation_user",
                ),
            )

        models.DMParticipant = DMParticipant
    elif not hasattr(models.DMParticipant, "device_ids"):
        device_ids = Column(JSON, nullable=True)
        models.DMParticipant.device_ids = device_ids
        if hasattr(models.DMParticipant, "__table__"):
            models.DMParticipant.__table__.append_column(device_ids)

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

            __table_args__ = (
                UniqueConstraint(
                    "message_id",
                    "recipient_user_id",
                    name="uq_dm_delivery_message_recipient",
                ),
            )

        models.DMDelivery = DMDelivery


_define_dm_models()

from routers.messaging import dm_conversations as dm_conversations_router

E2EEDevice = models.E2EEDevice
DMConversation = models.DMConversation
DMParticipant = models.DMParticipant
DMMessage = models.DMMessage


@pytest.fixture
def current_user(test_db):
    return test_db.query(User).first()


@pytest.fixture
def peer_user(test_db, current_user):
    return (
        test_db.query(User).filter(User.account_id != current_user.account_id).first()
    )


@pytest.fixture
def client(test_db, current_user, monkeypatch):
    app = FastAPI()
    app.include_router(dm_conversations_router.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: current_user

    monkeypatch.setattr(dm_conversations_router, "E2EE_DM_ENABLED", True)

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides = {}


def _create_device(test_db, user_id):
    device = E2EEDevice(user_id=user_id, status="active")
    test_db.add(device)
    test_db.commit()
    test_db.refresh(device)
    return device


def test_create_or_find_conversation(client, test_db, current_user, peer_user):
    _create_device(test_db, current_user.account_id)
    _create_device(test_db, peer_user.account_id)

    response = client.post(
        "/dm/conversations", json={"peer_user_id": peer_user.account_id}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"]
    assert len(payload["participants"]) == 2

    second = client.post(
        "/dm/conversations", json={"peer_user_id": peer_user.account_id}
    )
    assert second.status_code == 200
    assert second.json()["conversation_id"] == payload["conversation_id"]


def test_create_conversation_blocked(client, test_db, current_user, peer_user):
    test_db.add(
        Block(blocker_id=current_user.account_id, blocked_id=peer_user.account_id)
    )
    test_db.commit()

    response = client.post(
        "/dm/conversations", json={"peer_user_id": peer_user.account_id}
    )
    assert response.status_code == 403


def test_list_and_get_conversations(client, test_db, current_user, peer_user):
    conversation = DMConversation(id=uuid.uuid4(), created_at=datetime.utcnow())
    test_db.add(conversation)
    test_db.flush()

    participant1 = DMParticipant(
        conversation_id=conversation.id, user_id=current_user.account_id, device_ids=[]
    )
    participant2 = DMParticipant(
        conversation_id=conversation.id, user_id=peer_user.account_id, device_ids=[]
    )
    test_db.add_all([participant1, participant2])

    message = DMMessage(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        sender_user_id=peer_user.account_id,
        sender_device_id=uuid.uuid4(),
        ciphertext=b"ciphertext",
        proto=1,
        created_at=datetime.utcnow(),
    )
    test_db.add(message)
    test_db.commit()

    response = client.get("/dm/conversations")
    assert response.status_code == 200
    payload = response.json()
    assert payload["conversations"]
    assert payload["conversations"][0]["unread_count"] == 1

    response = client.get(f"/dm/conversations/{conversation.id}")
    assert response.status_code == 200
    assert response.json()["conversation_id"] == str(conversation.id)

    bad_response = client.get("/dm/conversations/not-a-uuid")
    assert bad_response.status_code == 400
