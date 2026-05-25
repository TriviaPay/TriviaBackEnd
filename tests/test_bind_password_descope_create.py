from datetime import date
from types import SimpleNamespace

import pytest
import routers.auth.service as auth_service
from fastapi import HTTPException
from models import User
from routers.auth.schemas import BindPasswordData
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


class _FakeMgmtUser:
    def __init__(
        self,
        existing_users=None,
        set_password_activates=True,
        has_set_active_password=True,
    ):
        self.create_calls = []
        self.load_calls = []
        self.password_calls = []
        self.active_password_calls = []
        self.update_calls = []
        self.set_password_activates = set_password_activates
        self.has_set_active_password = has_set_active_password
        self._last_loaded_user_id = None
        self.users = {}
        for user_id, payload in (existing_users or {}).items():
            user_payload = {"userId": user_id, "password": False}
            user_payload.update(payload)
            self.users[user_id] = user_payload

    def _find_user_id_by_login_id(self, login_id):
        login_id_lower = login_id.lower()
        for user_id, payload in self.users.items():
            login_ids = payload.get("loginIds") or []
            normalized_login_ids = [
                candidate.lower()
                for candidate in login_ids
                if isinstance(candidate, str)
            ]
            if login_id_lower in normalized_login_ids:
                return user_id
            email = payload.get("email")
            if isinstance(email, str) and email.lower() == login_id_lower:
                return user_id
        return None

    def load(self, user_id):
        self.load_calls.append(user_id)
        self._last_loaded_user_id = user_id
        if user_id in self.users:
            return {"user": dict(self.users[user_id])}
        if user_id == "session-user-id":
            raise Exception("User not found")
        raise AssertionError(f"Unexpected load() call for user_id={user_id}")

    def create(
        self,
        login_id,
        email=None,
        phone=None,
        display_name=None,
        given_name=None,
        middle_name=None,
        family_name=None,
        role_names=None,
        user_tenants=None,
        picture=None,
        custom_attributes=None,
        verified_email=None,
        verified_phone=None,
        invite_url=None,
        additional_login_ids=None,
        sso_app_ids=None,
    ):
        user_payload = {
            "userId": "created-descope-id",
            "loginIds": [login_id],
            "email": email,
            "password": False,
            "activePassword": False,
        }
        self.users["created-descope-id"] = user_payload
        self.create_calls.append(
            {
                "login_id": login_id,
                "email": email,
                "display_name": display_name,
                "custom_attributes": custom_attributes,
            }
        )
        return {"user": dict(user_payload)}

    def update(self, login_id, **kwargs):
        self.update_calls.append((login_id, kwargs))
        user_id = self._find_user_id_by_login_id(login_id) or self._last_loaded_user_id
        if not user_id or user_id not in self.users:
            return
        payload = self.users[user_id]
        new_email = kwargs.get("email") or login_id
        payload["email"] = new_email
        payload["loginIds"] = [new_email]
        if "display_name" in kwargs:
            payload["displayName"] = kwargs["display_name"]

    def _activate_password(self, login_id, password, *, call_list):
        call_list.append((login_id, password))
        if not self.set_password_activates:
            return
        user_id = self._find_user_id_by_login_id(login_id) or self._last_loaded_user_id
        if not user_id or user_id not in self.users:
            return
        payload = self.users[user_id]
        payload["loginIds"] = payload.get("loginIds") or [login_id]
        payload["password"] = True
        payload["activePassword"] = True

    def set_active_password(self, login_id, password):
        if not self.has_set_active_password:
            raise AttributeError("set_active_password")
        self._activate_password(
            login_id,
            password,
            call_list=self.active_password_calls,
        )

    def set_password(self, login_id, password):
        self._activate_password(login_id, password, call_list=self.password_calls)


class _FakeMgmtClient:
    def __init__(
        self,
        existing_users=None,
        set_password_activates=True,
        has_set_active_password=True,
    ):
        self.mgmt = SimpleNamespace(
            user=_FakeMgmtUser(
                existing_users,
                set_password_activates,
                has_set_active_password,
            )
        )


def _make_db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)
    User.__table__.create(bind=engine)
    return engine, SessionLocal()


def _make_request():
    return SimpleNamespace(
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer test-token",
        },
        client=SimpleNamespace(host="127.0.0.1"),
    )


def _configure_bind_password_test(monkeypatch, fake_client, jwt_payload):
    monkeypatch.setattr(auth_service, "mgmt_client", fake_client)
    monkeypatch.setattr(auth_service, "check_rate_limit", lambda identifier: True)
    monkeypatch.setattr(auth_service, "validate_descope_jwt", lambda token: jwt_payload)
    monkeypatch.setattr(auth_service, "STORE_PASSWORD_IN_DESCOPE", True)
    monkeypatch.setattr(auth_service, "STORE_PASSWORD_IN_NEONDB", False)
    monkeypatch.setattr(
        auth_service, "ensure_admin_conversation_and_message", lambda db, user: None
    )
    monkeypatch.setattr(auth_service, "_track_device_uuid", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_service, "get_unique_referral_code", lambda db: "REF123")
    monkeypatch.setattr(auth_service, "get_default_profile_pic_url", lambda username: None)


def test_bind_password_creates_descope_user_and_persists_returned_user_id(monkeypatch):
    engine, db = _make_db_session()
    fake_client = _FakeMgmtClient()

    _configure_bind_password_test(
        monkeypatch,
        fake_client,
        {
            "userId": "session-user-id",
            "loginIds": ["user_session-user-id@descope.local"],
        },
    )

    payload = BindPasswordData(
        email="testingcode81@gmail.com",
        password="Password1",
        username="testing1",
        country="China",
        date_of_birth=date(2000, 1, 1),
    )

    try:
        response = auth_service.bind_password(_make_request(), payload, db)
        created_user = db.query(User).filter(User.email == payload.email).first()

        assert response["success"] is True
        assert created_user is not None
        assert created_user.descope_user_id == "created-descope-id"
        assert created_user.username == "testing1"
        assert fake_client.mgmt.user.load_calls == [
            "session-user-id",
            "created-descope-id",
        ]
        assert fake_client.mgmt.user.active_password_calls == [
            ("testingcode81@gmail.com", "Password1")
        ]
        assert fake_client.mgmt.user.password_calls == []
        assert fake_client.mgmt.user.create_calls == [
            {
                "login_id": "testingcode81@gmail.com",
                "email": "testingcode81@gmail.com",
                "display_name": "testing1",
                "custom_attributes": {
                    "country": "China",
                    "date_of_birth": "2000-01-01",
                },
            }
        ]
    finally:
        db.close()
        User.__table__.drop(bind=engine)
        engine.dispose()


def test_bind_password_matches_existing_local_user_by_descope_id(monkeypatch):
    engine, db = _make_db_session()
    fake_client = _FakeMgmtClient(
        existing_users={
            "session-user-id": {
                "loginIds": ["user_session-user-id@descope.local"],
                "email": "user_session-user-id@descope.local",
                "password": False,
                "activePassword": False,
            }
        }
    )

    _configure_bind_password_test(
        monkeypatch,
        fake_client,
        {
            "userId": "session-user-id",
            "loginIds": ["user_session-user-id@descope.local"],
        },
    )

    db.add(
        User(
            descope_user_id="session-user-id",
            email="legacy-placeholder@descope.local",
            username="miragamingllc",
        )
    )
    db.commit()

    payload = BindPasswordData(
        email="miragamingllc@gmail.com",
        password="Password1",
        username="miragamingllc",
        country="United States",
        date_of_birth=date(2000, 1, 1),
    )

    try:
        response = auth_service.bind_password(_make_request(), payload, db)
        updated_user = (
            db.query(User).filter(User.descope_user_id == "session-user-id").first()
        )

        assert response["success"] is True
        assert updated_user is not None
        assert updated_user.email == "miragamingllc@gmail.com"
        assert updated_user.username == "miragamingllc"
        assert fake_client.mgmt.user.load_calls == ["session-user-id", "session-user-id"]
        assert fake_client.mgmt.user.create_calls == []
        assert fake_client.mgmt.user.update_calls == [
            (
                "miragamingllc@gmail.com",
                {
                    "email": "miragamingllc@gmail.com",
                    "display_name": "miragamingllc",
                    "custom_attributes": {
                        "country": "United States",
                        "date_of_birth": "2000-01-01",
                    },
                },
            )
        ]
        assert fake_client.mgmt.user.active_password_calls == [
            ("miragamingllc@gmail.com", "Password1")
        ]
        assert fake_client.mgmt.user.password_calls == []
    finally:
        db.close()
        User.__table__.drop(bind=engine)
        engine.dispose()


def test_bind_password_rejects_new_user_username_conflict_before_descope(monkeypatch):
    engine, db = _make_db_session()
    fake_client = _FakeMgmtClient()

    _configure_bind_password_test(
        monkeypatch,
        fake_client,
        {
            "userId": "new-session-user-id",
            "loginIds": ["user_new-session-user-id@descope.local"],
        },
    )

    db.add(User(email="taken@example.com", username="takenname"))
    db.commit()

    payload = BindPasswordData(
        email="new@example.com",
        password="Password1",
        username="takenname",
        country="United States",
        date_of_birth=date(2000, 1, 1),
    )

    try:
        with pytest.raises(HTTPException) as exc_info:
            auth_service.bind_password(_make_request(), payload, db)

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["error"] == "username_taken"
        assert fake_client.mgmt.user.load_calls == []
        assert fake_client.mgmt.user.create_calls == []
        assert fake_client.mgmt.user.update_calls == []
        assert fake_client.mgmt.user.active_password_calls == []
        assert fake_client.mgmt.user.password_calls == []
    finally:
        db.close()
        User.__table__.drop(bind=engine)
        engine.dispose()


def test_bind_password_rejects_existing_user_username_conflict_before_descope(
    monkeypatch,
):
    engine, db = _make_db_session()
    fake_client = _FakeMgmtClient(
        existing_users={
            "session-user-id": {
                "loginIds": ["current@example.com"],
                "email": "current@example.com",
                "password": True,
                "activePassword": True,
            }
        }
    )

    _configure_bind_password_test(
        monkeypatch,
        fake_client,
        {
            "userId": "session-user-id",
            "loginIds": ["current@example.com"],
        },
    )

    db.add(
        User(
            descope_user_id="session-user-id",
            email="current@example.com",
            username="currentname",
        )
    )
    db.add(User(email="taken@example.com", username="takenname"))
    db.commit()

    payload = BindPasswordData(
        email="current@example.com",
        password="Password1",
        username="takenname",
        country="United States",
        date_of_birth=date(2000, 1, 1),
    )

    try:
        with pytest.raises(HTTPException) as exc_info:
            auth_service.bind_password(_make_request(), payload, db)

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["error"] == "username_taken"
        assert fake_client.mgmt.user.load_calls == []
        assert fake_client.mgmt.user.create_calls == []
        assert fake_client.mgmt.user.update_calls == []
        assert fake_client.mgmt.user.active_password_calls == []
        assert fake_client.mgmt.user.password_calls == []
    finally:
        db.close()
        User.__table__.drop(bind=engine)
        engine.dispose()


def test_bind_password_fails_when_descope_password_cannot_be_verified(monkeypatch):
    engine, db = _make_db_session()
    fake_client = _FakeMgmtClient(
        existing_users={
            "session-user-id": {
                "loginIds": ["current@example.com"],
                "email": "current@example.com",
                "password": False,
                "activePassword": False,
            }
        },
        set_password_activates=False,
    )

    _configure_bind_password_test(
        monkeypatch,
        fake_client,
        {
            "userId": "session-user-id",
            "loginIds": ["current@example.com"],
        },
    )

    db.add(
        User(
            descope_user_id="session-user-id",
            email="current@example.com",
            username="currentname",
        )
    )
    db.commit()

    payload = BindPasswordData(
        email="current@example.com",
        password="Password1",
        username="currentname",
        country="United States",
        date_of_birth=date(2000, 1, 1),
    )

    try:
        with pytest.raises(HTTPException) as exc_info:
            auth_service.bind_password(_make_request(), payload, db)

        assert exc_info.value.status_code == 500
        assert (
            exc_info.value.detail
            == "Password binding could not be verified in authentication system"
        )
        assert fake_client.mgmt.user.load_calls == ["session-user-id", "session-user-id"]
        assert fake_client.mgmt.user.active_password_calls == [
            ("current@example.com", "Password1")
        ]
        assert fake_client.mgmt.user.password_calls == []
    finally:
        db.close()
        User.__table__.drop(bind=engine)
        engine.dispose()


def test_bind_password_falls_back_when_sdk_only_exposes_set_password(monkeypatch):
    engine, db = _make_db_session()
    fake_client = _FakeMgmtClient(
        existing_users={
            "session-user-id": {
                "loginIds": ["fallback@example.com"],
                "email": "fallback@example.com",
                "password": False,
                "activePassword": False,
            }
        },
        has_set_active_password=False,
    )

    _configure_bind_password_test(
        monkeypatch,
        fake_client,
        {
            "userId": "session-user-id",
            "loginIds": ["fallback@example.com"],
        },
    )

    db.add(
        User(
            descope_user_id="session-user-id",
            email="fallback@example.com",
            username="fallbackuser",
        )
    )
    db.commit()

    payload = BindPasswordData(
        email="fallback@example.com",
        password="Password1",
        username="fallbackuser",
        country="United States",
        date_of_birth=date(2000, 1, 1),
    )

    try:
        response = auth_service.bind_password(_make_request(), payload, db)

        assert response["success"] is True
        assert fake_client.mgmt.user.active_password_calls == []
        assert fake_client.mgmt.user.password_calls == [
            ("fallback@example.com", "Password1")
        ]
    finally:
        db.close()
        User.__table__.drop(bind=engine)
        engine.dispose()
