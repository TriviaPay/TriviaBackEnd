from datetime import datetime, timedelta
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
)
from sqlalchemy.dialects.postgresql import UUID

import models
from db import get_db
from models import Base, User
from routers.dependencies import get_current_user


def _define_status_models():
    if not hasattr(models, "StatusPost"):
        class StatusPost(Base):
            __tablename__ = "status_posts"

            id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
            owner_user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
            created_at = Column(DateTime, default=datetime.utcnow)
            expires_at = Column(DateTime, nullable=True)
            media_meta = Column(JSON, nullable=True)
            audience_mode = Column(Integer, nullable=False, default=0)
            post_epoch = Column(Integer, nullable=False, default=0)

        models.StatusPost = StatusPost

    if not hasattr(models, "StatusAudience"):
        class StatusAudience(Base):
            __tablename__ = "status_audience"

            post_id = Column(UUID(as_uuid=True), ForeignKey("status_posts.id"), primary_key=True)
            viewer_user_id = Column(BigInteger, ForeignKey("users.account_id"), primary_key=True)

        models.StatusAudience = StatusAudience

    if not hasattr(models, "StatusView"):
        class StatusView(Base):
            __tablename__ = "status_views"

            post_id = Column(UUID(as_uuid=True), ForeignKey("status_posts.id"), primary_key=True)
            viewer_user_id = Column(BigInteger, ForeignKey("users.account_id"), primary_key=True)
            viewed_at = Column(DateTime, nullable=True)

        models.StatusView = StatusView


_define_status_models()

from routers import status_metrics as status_metrics_router

StatusPost = models.StatusPost
StatusAudience = models.StatusAudience
StatusView = models.StatusView


@pytest.fixture
def admin_user(test_db):
    user = test_db.query(User).first()
    user.is_admin = True
    test_db.commit()
    return user


@pytest.fixture
def client(test_db, admin_user, monkeypatch):
    app = FastAPI()
    app.include_router(status_metrics_router.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: admin_user
    monkeypatch.setattr(status_metrics_router, "STATUS_ENABLED", True)

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides = {}


def test_metrics_admin_required(test_db, admin_user, monkeypatch):
    app = FastAPI()
    app.include_router(status_metrics_router.router)

    def override_get_db():
        yield test_db

    non_admin = test_db.query(User).filter(User.account_id != admin_user.account_id).first()
    non_admin.is_admin = False
    test_db.commit()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: non_admin
    monkeypatch.setattr(status_metrics_router, "STATUS_ENABLED", True)

    with TestClient(app) as test_client:
        response = test_client.get("/status/metrics")
        assert response.status_code == 403


def test_metrics_counts_and_average(client, test_db, admin_user):
    now = datetime.utcnow()
    post_active = StatusPost(
        id=uuid.uuid4(),
        owner_user_id=admin_user.account_id,
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=1),
        media_meta={}
    )
    post_expired = StatusPost(
        id=uuid.uuid4(),
        owner_user_id=admin_user.account_id,
        created_at=now - timedelta(days=1),
        expires_at=now - timedelta(minutes=1),
        media_meta={}
    )
    test_db.add_all([post_active, post_expired])
    test_db.flush()

    test_db.add_all([
        StatusAudience(post_id=post_active.id, viewer_user_id=admin_user.account_id),
        StatusAudience(post_id=post_active.id, viewer_user_id=admin_user.account_id + 1),
        StatusAudience(post_id=post_expired.id, viewer_user_id=admin_user.account_id),
    ])
    test_db.add(StatusView(post_id=post_active.id, viewer_user_id=admin_user.account_id, viewed_at=now))
    test_db.commit()

    response = client.get("/status/metrics")
    assert response.status_code == 200
    payload = response.json()["metrics"]
    assert payload["posts"]["today"] == 1
    assert payload["posts"]["active"] == 1
    assert payload["posts"]["expired"] == 1
    assert payload["views"]["today"] == 1
    assert payload["audience"]["average_size"] == 1.5
