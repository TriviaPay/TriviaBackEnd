from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.db import get_db
from models import Notification, User
from routers.dependencies import get_current_user
from routers.notifications import notifications as notifications_router


@pytest.fixture
def current_user(test_db):
    return test_db.query(User).first()


@pytest.fixture
def other_user(test_db, current_user):
    return (
        test_db.query(User).filter(User.account_id != current_user.account_id).first()
    )


@pytest.fixture
def client(test_db, current_user):
    app = FastAPI()
    app.include_router(notifications_router.router)

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: current_user

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides = {}


def _create_notification(
    test_db, user_id, *, title="Title", body="Body", read=False, created_at=None
):
    notification = Notification(
        user_id=user_id,
        title=title,
        body=body,
        type="test",
        data={"kind": "test"},
        read=read,
        read_at=(created_at or datetime.utcnow()) if read else None,
    )
    if created_at is not None:
        notification.created_at = created_at
    test_db.add(notification)
    test_db.commit()
    test_db.refresh(notification)
    return notification


def test_list_notifications_counts_and_cursor(
    client, test_db, current_user, other_user
):
    now = datetime.utcnow()
    newest = _create_notification(
        test_db, current_user.account_id, created_at=now, read=False
    )
    older = _create_notification(
        test_db,
        current_user.account_id,
        created_at=now - timedelta(minutes=5),
        read=True,
    )
    _create_notification(
        test_db,
        other_user.account_id,
        created_at=now - timedelta(minutes=10),
        read=False,
    )

    response = client.get("/notifications?limit=2")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["unread_count"] == 1
    assert len(payload["notifications"]) == 2
    assert payload["notifications"][0]["id"] == newest.id

    response = client.get("/notifications?unread_only=true")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["unread_count"] == 1
    assert payload["notifications"][0]["id"] == newest.id

    cursor = f"{payload['notifications'][0]['created_at']}|{payload['notifications'][0]['id']}"
    response = client.get(f"/notifications?cursor={cursor}&limit=10")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["unread_count"] == 1
    assert len(payload["notifications"]) == 1
    assert payload["notifications"][0]["id"] == older.id


def test_mark_read_errors_and_success(client, test_db, current_user, other_user):
    response = client.put("/notifications/mark-read", json={"notification_ids": []})
    assert response.status_code == 400

    other_notification = _create_notification(
        test_db, other_user.account_id, read=False
    )
    response = client.put(
        "/notifications/mark-read", json={"notification_ids": [other_notification.id]}
    )
    assert response.status_code == 404

    notification = _create_notification(test_db, current_user.account_id, read=False)
    response = client.put(
        "/notifications/mark-read", json={"notification_ids": [notification.id]}
    )
    assert response.status_code == 200
    test_db.refresh(notification)
    assert notification.read is True
    assert notification.read_at is not None


def test_mark_all_and_delete_flows(client, test_db, current_user):
    unread = _create_notification(test_db, current_user.account_id, read=False)
    read = _create_notification(test_db, current_user.account_id, read=True)

    response = client.put("/notifications/mark-all-read")
    assert response.status_code == 200
    test_db.refresh(unread)
    assert unread.read is True

    response = client.delete(f"/notifications/{read.id}")
    assert response.status_code == 200

    response = client.delete("/notifications?read_only=true")
    assert response.status_code == 200
    remaining = (
        test_db.query(Notification)
        .filter(Notification.user_id == current_user.account_id)
        .count()
    )
    assert remaining == 0


def test_create_test_notification(client, test_db, current_user):
    response = client.post("/notifications/test", json={"title": "Hi", "body": "There"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "Hi"
    assert payload["body"] == "There"
    assert (
        test_db.query(Notification)
        .filter(Notification.user_id == current_user.account_id)
        .count()
        == 1
    )
