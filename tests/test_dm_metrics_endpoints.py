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
    LargeBinary,
    String,
)
from sqlalchemy.dialects.postgresql import UUID

import models
from db import get_db
from models import Base, User
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
            last_seen_at = Column(DateTime, nullable=True)

        models.E2EEDevice = E2EEDevice

    if not hasattr(models, "E2EEKeyBundle"):
        class E2EEKeyBundle(Base):
            __tablename__ = "e2ee_key_bundles"

            device_id = Column(UUID(as_uuid=True), ForeignKey("e2ee_devices.device_id"), primary_key=True)
            identity_key_pub = Column(String, nullable=False)
            signed_prekey_pub = Column(String, nullable=False)
            signed_prekey_sig = Column(String, nullable=False)
            bundle_version = Column(Integer, default=1, nullable=False)
            prekeys_remaining = Column(Integer, default=0, nullable=False)
            updated_at = Column(DateTime, default=datetime.utcnow)

        models.E2EEKeyBundle = E2EEKeyBundle

    if not hasattr(models, "E2EEOneTimePrekey"):
        class E2EEOneTimePrekey(Base):
            __tablename__ = "e2ee_one_time_prekeys"

            id = Column(Integer, primary_key=True)
            device_id = Column(UUID(as_uuid=True), ForeignKey("e2ee_devices.device_id"), nullable=False)
            prekey_pub = Column(String, nullable=False)
            claimed = Column(Boolean, default=False, nullable=False)
            created_at = Column(DateTime, default=datetime.utcnow)

        models.E2EEOneTimePrekey = E2EEOneTimePrekey

    if not hasattr(models, "DMMessage"):
        class DMMessage(Base):
            __tablename__ = "dm_messages"

            id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
            conversation_id = Column(UUID(as_uuid=True), nullable=False)
            sender_user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
            sender_device_id = Column(UUID(as_uuid=True), nullable=False)
            ciphertext = Column(LargeBinary, nullable=False)
            proto = Column(Integer, nullable=False)
            created_at = Column(DateTime, default=datetime.utcnow)

        models.DMMessage = DMMessage

    if not hasattr(models, "DMDelivery"):
        class DMDelivery(Base):
            __tablename__ = "dm_delivery"

            id = Column(Integer, primary_key=True)
            message_id = Column(UUID(as_uuid=True), ForeignKey("dm_messages.id"), nullable=False)
            recipient_user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
            delivered_at = Column(DateTime, nullable=True)
            read_at = Column(DateTime, nullable=True)

        models.DMDelivery = DMDelivery


_define_dm_models()

dm_metrics = importlib.import_module("routers.dm_metrics")
dm_metrics = importlib.reload(dm_metrics)

E2EEDevice = models.E2EEDevice
E2EEKeyBundle = models.E2EEKeyBundle
E2EEOneTimePrekey = models.E2EEOneTimePrekey
DMMessage = models.DMMessage
DMDelivery = models.DMDelivery


def _make_client(test_db, user, monkeypatch):
    app = FastAPI()
    app.include_router(dm_metrics.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(dm_metrics, "E2EE_DM_ENABLED", True)
    dm_metrics._metrics_cache["payload"] = None
    dm_metrics._metrics_cache["ts"] = 0.0
    return TestClient(app)


def test_dm_metrics_requires_admin(test_db, monkeypatch):
    user = test_db.query(User).first()
    user.is_admin = False
    test_db.commit()

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.get("/dm/metrics")

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required"


def test_dm_metrics_payload(test_db, monkeypatch):
    user = test_db.query(User).first()
    user.is_admin = True
    test_db.commit()

    device_id = uuid.uuid4()
    device = E2EEDevice(
        device_id=device_id,
        user_id=user.account_id,
        device_name="device",
        status="active",
    )
    bundle = E2EEKeyBundle(
        device_id=device_id,
        identity_key_pub="identity",
        signed_prekey_pub="signed",
        signed_prekey_sig="sig",
        bundle_version=1,
        prekeys_remaining=1,
        updated_at=datetime.utcnow(),
    )
    test_db.add_all([device, bundle])
    test_db.add(E2EEOneTimePrekey(device_id=device_id, prekey_pub="prekey-1", claimed=False))
    test_db.add(E2EEOneTimePrekey(device_id=device_id, prekey_pub="prekey-2", claimed=True))

    now = datetime.utcnow()
    message_recent = DMMessage(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        sender_user_id=user.account_id,
        sender_device_id=device_id,
        ciphertext=b"cipher",
        proto=1,
        created_at=now - timedelta(minutes=10),
    )
    message_old = DMMessage(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        sender_user_id=user.account_id,
        sender_device_id=device_id,
        ciphertext=b"cipher",
        proto=1,
        created_at=now - timedelta(days=1, minutes=5),
    )
    test_db.add_all([message_recent, message_old])
    test_db.add(
        DMDelivery(
            message_id=message_recent.id,
            recipient_user_id=user.account_id,
            delivered_at=now - timedelta(minutes=5),
            read_at=None,
        )
    )
    test_db.add(
        DMDelivery(
            message_id=message_old.id,
            recipient_user_id=user.account_id,
            delivered_at=None,
            read_at=None,
        )
    )
    test_db.commit()

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.get("/dm/metrics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["metrics"]["messages"]["today"] >= 1
    assert payload["metrics"]["delivery"]["undelivered"] >= 1


def test_dm_metrics_cache_hit(test_db, monkeypatch):
    user = test_db.query(User).first()
    user.is_admin = True
    test_db.commit()

    cached = {"status": "success", "metrics": {"messages": {"today": 0, "last_hour": 0}}}

    with _make_client(test_db, user, monkeypatch) as client:
        dm_metrics._metrics_cache["payload"] = cached
        dm_metrics._metrics_cache["ts"] = datetime.utcnow().timestamp()
        response = client.get("/dm/metrics")

    assert response.status_code == 200
    assert response.json()["metrics"]["messages"]["today"] == 0
