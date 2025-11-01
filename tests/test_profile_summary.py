import pytest
from fastapi.testclient import TestClient

from models import User, Avatar, Frame
from main import app
from routers.dependencies import get_current_user


def test_profile_summary_returns_expected_fields(test_db):
    # Arrange: create a user with selected avatar and frame
    user = test_db.query(User).first()
    avatar = Avatar(id="av1", name="Avatar One", description=None, image_url="https://img/avatar1.png")
    frame = Frame(id="fr1", name="Frame One", description=None, image_url="https://img/frame1.png")
    test_db.add_all([avatar, frame])
    test_db.flush()

    user.selected_avatar_id = avatar.id
    user.selected_frame_id = frame.id
    user.street_1 = "123 Main St"
    user.street_2 = "Unit 5"
    user.suite_or_apt_number = "A5"
    user.city = "Metropolis"
    user.state = "CA"
    user.country = "USA"
    user.zip = "90210"
    test_db.commit()

    # Override auth dependency to return this user
    app.dependency_overrides[get_current_user] = lambda: user

    client = TestClient(app)

    # Act
    resp = client.get("/profile/summary")

    # Cleanup override
    app.dependency_overrides.pop(get_current_user, None)

    # Assert
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["username"] == user.username
    assert data["account_id"] == user.account_id
    assert data["email"] == user.email
    assert data["address1"] == "123 Main St"
    assert data["address2"] == "Unit 5"
    assert data["apt_number"] == "A5"
    assert data["city"] == "Metropolis"
    assert data["state"] == "CA"
    assert data["country"] == "USA"
    assert data["zip"] == "90210"
    assert data["avatar"]["id"] == avatar.id
    assert data["frame"]["id"] == frame.id


