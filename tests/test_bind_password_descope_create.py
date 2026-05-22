from datetime import date
from types import SimpleNamespace

import routers.auth.service as auth_service
from models import User
from routers.auth.schemas import BindPasswordData
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


class _FakeMgmtUser:
    def __init__(self):
        self.create_calls = []
        self.load_calls = []
        self.password_calls = []

    def load(self, user_id):
        self.load_calls.append(user_id)
        if user_id == "session-user-id":
            raise Exception("User not found")
        if user_id == "created-descope-id":
            return {"user": {"userId": "created-descope-id", "password": True}}
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
        self.create_calls.append(
            {
                "login_id": login_id,
                "email": email,
                "display_name": display_name,
                "custom_attributes": custom_attributes,
            }
        )
        return {
            "user": {
                "userId": "created-descope-id",
                "loginIds": [login_id],
                "email": email,
                "password": False,
            }
        }

    def set_password(self, login_id, password):
        self.password_calls.append((login_id, password))


class _FakeMgmtClient:
    def __init__(self):
        self.mgmt = SimpleNamespace(user=_FakeMgmtUser())


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


def test_bind_password_creates_descope_user_and_persists_returned_user_id(monkeypatch):
    engine, db = _make_db_session()
    fake_client = _FakeMgmtClient()

    monkeypatch.setattr(auth_service, "mgmt_client", fake_client)
    monkeypatch.setattr(auth_service, "check_rate_limit", lambda identifier: True)
    monkeypatch.setattr(
        auth_service,
        "validate_descope_jwt",
        lambda token: {
            "userId": "session-user-id",
            "loginIds": ["user_session-user-id@descope.local"],
        },
    )
    monkeypatch.setattr(auth_service, "STORE_PASSWORD_IN_DESCOPE", True)
    monkeypatch.setattr(auth_service, "STORE_PASSWORD_IN_NEONDB", False)
    monkeypatch.setattr(
        auth_service, "ensure_admin_conversation_and_message", lambda db, user: None
    )
    monkeypatch.setattr(auth_service, "_track_device_uuid", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_service, "get_unique_referral_code", lambda db: "REF123")
    monkeypatch.setattr(auth_service, "get_default_profile_pic_url", lambda username: None)

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
        assert fake_client.mgmt.user.password_calls == [
            ("testingcode81@gmail.com", "Password1")
        ]
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
