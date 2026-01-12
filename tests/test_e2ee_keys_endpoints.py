import importlib
import uuid
from datetime import datetime

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
from models import Base, Block, User
from routers.dependencies import get_current_user


def _define_e2ee_models():
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

            device_id = Column(
                UUID(as_uuid=True),
                ForeignKey("e2ee_devices.device_id"),
                primary_key=True,
            )
            identity_key_pub = Column(String, nullable=False)
            signed_prekey_pub = Column(String, nullable=False)
            signed_prekey_sig = Column(String, nullable=False)
            bundle_version = Column(Integer, default=1, nullable=False)
            prekeys_remaining = Column(Integer, default=0, nullable=False)
            created_at = Column(DateTime, default=datetime.utcnow)
            updated_at = Column(DateTime, default=datetime.utcnow)

        models.E2EEKeyBundle = E2EEKeyBundle

    if not hasattr(models, "E2EEOneTimePrekey"):

        class E2EEOneTimePrekey(Base):
            __tablename__ = "e2ee_one_time_prekeys"

            id = Column(Integer, primary_key=True)
            device_id = Column(
                UUID(as_uuid=True), ForeignKey("e2ee_devices.device_id"), nullable=False
            )
            prekey_pub = Column(String, nullable=False)
            claimed = Column(Boolean, default=False, nullable=False)
            created_at = Column(DateTime, default=datetime.utcnow)

        models.E2EEOneTimePrekey = E2EEOneTimePrekey

    if not hasattr(models, "DeviceRevocation"):

        class DeviceRevocation(Base):
            __tablename__ = "device_revocations"

            id = Column(Integer, primary_key=True)
            user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
            device_id = Column(UUID(as_uuid=True), nullable=False)
            reason = Column(String, nullable=True)
            created_at = Column(DateTime, default=datetime.utcnow)

        models.DeviceRevocation = DeviceRevocation

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
            conversation_id = Column(
                UUID(as_uuid=True), ForeignKey("dm_conversations.id"), nullable=False
            )
            user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)

        models.DMParticipant = DMParticipant


_define_e2ee_models()

e2ee_keys = importlib.import_module("routers.messaging.e2ee_keys")
e2ee_keys = importlib.reload(e2ee_keys)

E2EEDevice = models.E2EEDevice
E2EEKeyBundle = models.E2EEKeyBundle
E2EEOneTimePrekey = models.E2EEOneTimePrekey
DeviceRevocation = models.DeviceRevocation
DMConversation = models.DMConversation
DMParticipant = models.DMParticipant


@pytest.fixture
def users(test_db):
    first = test_db.query(User).first()
    second = test_db.query(User).filter(User.account_id != first.account_id).first()
    return first, second


def _make_client(test_db, user, monkeypatch):
    app = FastAPI()
    app.include_router(e2ee_keys.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user

    monkeypatch.setattr(e2ee_keys, "E2EE_DM_ENABLED", True)

    return TestClient(app)


def _payload(device_id=None, identity="identity-1", prekeys=1):
    return {
        "device_id": str(device_id) if device_id else None,
        "device_name": "iPhone",
        "identity_key_pub": identity,
        "signed_prekey_pub": "signed-prekey",
        "signed_prekey_sig": "signed-prekey-sig",
        "one_time_prekeys": [{"prekey_pub": f"prekey-{idx}"} for idx in range(prekeys)],
    }


def _create_device_bundle(
    test_db, user_id, prekeys=1, bundle_version=1, device_id=None
):
    device_id = device_id or uuid.uuid4()
    device = E2EEDevice(
        device_id=device_id,
        user_id=user_id,
        device_name="device",
        status="active",
    )
    bundle = E2EEKeyBundle(
        device_id=device_id,
        identity_key_pub="identity",
        signed_prekey_pub="signed",
        signed_prekey_sig="sig",
        bundle_version=bundle_version,
        prekeys_remaining=prekeys,
    )
    test_db.add_all([device, bundle])
    for idx in range(prekeys):
        test_db.add(
            E2EEOneTimePrekey(
                device_id=device_id, prekey_pub=f"prekey-{idx}", claimed=False
            )
        )
    test_db.commit()
    return device_id


def test_upload_key_bundle_success(test_db, users, monkeypatch):
    user, _ = users
    with _make_client(test_db, user, monkeypatch) as client:
        response = client.post("/e2ee/keys/upload", json=_payload(prekeys=2))

    assert response.status_code == 200
    payload = response.json()
    assert payload["prekeys_stored"] == 2

    bundle = test_db.query(E2EEKeyBundle).first()
    assert bundle.prekeys_remaining == 2
    assert test_db.query(E2EEOneTimePrekey).count() == 2


def test_upload_key_bundle_too_many_prekeys(test_db, users, monkeypatch):
    user, _ = users
    monkeypatch.setattr(e2ee_keys, "E2EE_DM_PREKEY_POOL_SIZE", 1)

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.post("/e2ee/keys/upload", json=_payload(prekeys=2))

    assert response.status_code == 400


def test_upload_key_bundle_invalid_device_id(test_db, users, monkeypatch):
    user, _ = users
    payload = _payload(prekeys=1)
    payload["device_id"] = "not-a-uuid"

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.post("/e2ee/keys/upload", json=payload)

    assert response.status_code == 400


def test_upload_key_bundle_identity_change_blocked(test_db, users, monkeypatch):
    user, _ = users
    monkeypatch.setattr(e2ee_keys, "E2EE_DM_IDENTITY_CHANGE_BLOCK_THRESHOLD", 1)
    monkeypatch.setattr(e2ee_keys, "E2EE_DM_IDENTITY_CHANGE_ALERT_THRESHOLD", 0)

    device_id = uuid.uuid4()

    with _make_client(test_db, user, monkeypatch) as client:
        first = client.post(
            "/e2ee/keys/upload",
            json=_payload(device_id=device_id, identity="identity-1"),
        )
        assert first.status_code == 200

        second = client.post(
            "/e2ee/keys/upload",
            json=_payload(device_id=device_id, identity="identity-2"),
        )

    assert second.status_code == 409
    assert second.json()["detail"] == "IDENTITY_CHANGE_BLOCKED"

    device = test_db.query(E2EEDevice).filter(E2EEDevice.device_id == device_id).first()
    assert device.status == "revoked"
    assert test_db.query(DeviceRevocation).count() >= 1


def test_get_key_bundle_requires_relationship(test_db, users, monkeypatch):
    user1, user2 = users
    _create_device_bundle(test_db, user2.account_id, prekeys=1)

    with _make_client(test_db, user1, monkeypatch) as client:
        response = client.get(f"/e2ee/keys/bundle?user_id={user2.account_id}")

    assert response.status_code == 403
    assert response.json()["detail"] == "RELATIONSHIP_REQUIRED"


def test_get_key_bundle_with_relationship(test_db, users, monkeypatch):
    user1, user2 = users
    conversation = DMConversation()
    test_db.add(conversation)
    test_db.flush()
    test_db.add_all(
        [
            DMParticipant(conversation_id=conversation.id, user_id=user1.account_id),
            DMParticipant(conversation_id=conversation.id, user_id=user2.account_id),
        ]
    )
    test_db.commit()

    _create_device_bundle(test_db, user2.account_id, prekeys=2, bundle_version=2)

    with _make_client(test_db, user1, monkeypatch) as client:
        response = client.get(
            f"/e2ee/keys/bundle?user_id={user2.account_id}&bundle_version=1"
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "BUNDLE_STALE"


def test_get_key_bundle_self_without_relationship(test_db, users, monkeypatch):
    user, _ = users
    _create_device_bundle(test_db, user.account_id, prekeys=1, bundle_version=1)

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.get(f"/e2ee/keys/bundle?user_id={user.account_id}")

    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 1


def test_get_key_bundle_blocked(test_db, users, monkeypatch):
    user1, user2 = users
    _create_device_bundle(test_db, user2.account_id, prekeys=1)
    test_db.add(Block(blocker_id=user1.account_id, blocked_id=user2.account_id))
    test_db.commit()

    with _make_client(test_db, user1, monkeypatch) as client:
        response = client.get(f"/e2ee/keys/bundle?user_id={user2.account_id}")

    assert response.status_code == 403
    assert response.json()["detail"] == "BLOCKED"


def test_list_devices_returns_devices(test_db, users, monkeypatch):
    user, _ = users
    device_id = _create_device_bundle(test_db, user.account_id, prekeys=1)

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.get("/e2ee/devices")

    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 1
    assert devices[0]["device_id"] == str(device_id)


def test_revoke_device_success(test_db, users, monkeypatch):
    user, _ = users
    device_id = _create_device_bundle(test_db, user.account_id, prekeys=0)

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.post(
            "/e2ee/devices/revoke", json={"device_id": str(device_id), "reason": "lost"}
        )

    assert response.status_code == 200
    device = test_db.query(E2EEDevice).filter(E2EEDevice.device_id == device_id).first()
    assert device.status == "revoked"
    assert test_db.query(DeviceRevocation).count() == 1


def test_claim_prekey_success(test_db, users, monkeypatch):
    user, _ = users
    device_id = _create_device_bundle(test_db, user.account_id, prekeys=1)
    prekey = (
        test_db.query(E2EEOneTimePrekey)
        .filter(E2EEOneTimePrekey.device_id == device_id)
        .first()
    )

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.post(
            "/e2ee/prekeys/claim",
            json={"device_id": str(device_id), "prekey_id": prekey.id},
        )

    assert response.status_code == 200
    updated = (
        test_db.query(E2EEOneTimePrekey)
        .filter(E2EEOneTimePrekey.id == prekey.id)
        .first()
    )
    assert updated.claimed is True
    bundle = (
        test_db.query(E2EEKeyBundle)
        .filter(E2EEKeyBundle.device_id == device_id)
        .first()
    )
    assert bundle.prekeys_remaining == 0


def test_claim_prekey_exhausted(test_db, users, monkeypatch):
    user, _ = users
    device_id = _create_device_bundle(test_db, user.account_id, prekeys=0)
    prekey = E2EEOneTimePrekey(device_id=device_id, prekey_pub="prekey-1", claimed=True)
    test_db.add(prekey)
    test_db.commit()

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.post(
            "/e2ee/prekeys/claim",
            json={"device_id": str(device_id), "prekey_id": prekey.id},
        )

    assert response.status_code == 409
    assert response.headers.get("X-Error-Code") == "PREKEYS_EXHAUSTED"


def test_claim_prekey_not_found_with_available(test_db, users, monkeypatch):
    user, _ = users
    device_id = _create_device_bundle(test_db, user.account_id, prekeys=1)

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.post(
            "/e2ee/prekeys/claim",
            json={"device_id": str(device_id), "prekey_id": 9999},
        )

    assert response.status_code == 404


def test_claim_prekey_device_revoked(test_db, users, monkeypatch):
    user, _ = users
    device_id = uuid.uuid4()
    device = E2EEDevice(
        device_id=device_id,
        user_id=user.account_id,
        device_name="device",
        status="revoked",
    )
    test_db.add(device)
    test_db.commit()

    with _make_client(test_db, user, monkeypatch) as client:
        response = client.post(
            "/e2ee/prekeys/claim",
            json={"device_id": str(device_id), "prekey_id": 1},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "DEVICE_REVOKED"


def test_claim_prekey_requires_relationship(test_db, users, monkeypatch):
    user1, user2 = users
    device_id = _create_device_bundle(test_db, user2.account_id, prekeys=1)
    prekey = (
        test_db.query(E2EEOneTimePrekey)
        .filter(E2EEOneTimePrekey.device_id == device_id)
        .first()
    )

    with _make_client(test_db, user1, monkeypatch) as client:
        response = client.post(
            "/e2ee/prekeys/claim",
            json={"device_id": str(device_id), "prekey_id": prekey.id},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "RELATIONSHIP_REQUIRED"


def test_claim_prekey_blocked(test_db, users, monkeypatch):
    user1, user2 = users
    device_id = _create_device_bundle(test_db, user2.account_id, prekeys=1)
    prekey = (
        test_db.query(E2EEOneTimePrekey)
        .filter(E2EEOneTimePrekey.device_id == device_id)
        .first()
    )
    test_db.add(Block(blocker_id=user2.account_id, blocked_id=user1.account_id))
    test_db.commit()

    with _make_client(test_db, user1, monkeypatch) as client:
        response = client.post(
            "/e2ee/prekeys/claim",
            json={"device_id": str(device_id), "prekey_id": prekey.id},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "BLOCKED"
