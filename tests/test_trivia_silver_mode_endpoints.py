import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from db import get_db
from main import app
from models import (
    SubscriptionPlan,
    TriviaModeConfig,
    TriviaQuestionsSilverMode,
    TriviaQuestionsSilverModeDaily,
    TriviaSilverModeLeaderboard,
    TriviaSilverModeWinners,
    TriviaUserSilverModeDaily,
    User,
    UserSubscription,
)
from routers.dependencies import get_current_user
from utils.trivia_mode_service import get_active_draw_date, get_date_range_for_query


@pytest.fixture(autouse=True)
def draw_time_env(monkeypatch):
    monkeypatch.setenv("DRAW_TIMEZONE", "UTC")
    monkeypatch.setenv("DRAW_TIME_HOUR", "23")
    monkeypatch.setenv("DRAW_TIME_MINUTE", "59")


@pytest.fixture
def current_user(test_db):
    return test_db.query(User).first()


@pytest.fixture
def client(test_db, current_user):
    previous_overrides = app.dependency_overrides.copy()

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: current_user

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides = previous_overrides


def _add_silver_mode_config(test_db, badge_url=None):
    config = TriviaModeConfig(
        mode_id="silver",
        mode_name="Silver Mode - First-Come Reward",
        questions_count=1,
        reward_distribution=json.dumps(
            {
                "reward_type": "money",
                "distribution_method": "harmonic_sum",
                "requires_subscription": True,
                "subscription_amount": 10.0,
                "profit_share_percentage": 0.5,
            }
        ),
        amount=10.0,
        leaderboard_types=json.dumps(["daily"]),
        ad_config=json.dumps({}),
        survey_config=json.dumps({}),
        badge_image_url=badge_url,
    )
    test_db.add(config)
    test_db.commit()
    return config


def _add_silver_subscription(test_db, user):
    plan = SubscriptionPlan(
        name="Silver Plan",
        description="Silver Plan",
        price_usd=10.0,
        billing_interval="month",
        unit_amount_minor=1000,
        currency="usd",
        interval="month",
        interval_count=1,
    )
    test_db.add(plan)
    test_db.flush()

    subscription = UserSubscription(
        user_id=user.account_id,
        plan_id=plan.id,
        status="active",
        current_period_start=datetime.utcnow() - timedelta(days=1),
        current_period_end=datetime.utcnow() + timedelta(days=30),
    )
    test_db.add(subscription)
    test_db.commit()
    return subscription


def _create_silver_question(test_db, index=1):
    question = TriviaQuestionsSilverMode(
        question=f"Silver question {index}",
        option_a="A",
        option_b="B",
        option_c="C",
        option_d="D",
        correct_answer="A",
        hint="hint",
        explanation="explanation",
        category="general",
        difficulty_level="easy",
        question_hash=f"silver-hash-{index}",
        is_used=False,
    )
    test_db.add(question)
    test_db.commit()
    return question


def test_silver_mode_question_auto_allocates(client, test_db, current_user):
    _add_silver_mode_config(test_db)
    _add_silver_subscription(test_db, current_user)
    question = _create_silver_question(test_db)

    response = client.get("/trivia/silver-mode/question")

    assert response.status_code == 200
    payload = response.json()["question"]
    assert payload["question_id"] == question.id

    target_date = get_active_draw_date()
    start_dt, end_dt = get_date_range_for_query(target_date)
    daily_count = (
        test_db.query(TriviaQuestionsSilverModeDaily)
        .filter(
            TriviaQuestionsSilverModeDaily.date >= start_dt,
            TriviaQuestionsSilverModeDaily.date <= end_dt,
        )
        .count()
    )
    assert daily_count == 1


def test_silver_mode_submit_answer_creates_attempt(client, test_db, current_user):
    _add_silver_mode_config(test_db)
    _add_silver_subscription(test_db, current_user)
    question = _create_silver_question(test_db)
    target_date = get_active_draw_date()
    start_dt, _ = get_date_range_for_query(target_date)
    test_db.add(
        TriviaQuestionsSilverModeDaily(
            date=start_dt,
            question_id=question.id,
            question_order=1,
            is_used=False,
        )
    )
    test_db.commit()

    response = client.post(
        "/trivia/silver-mode/submit-answer",
        json={"question_id": question.id, "answer": "A"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["is_correct"] is True

    attempt = test_db.query(TriviaUserSilverModeDaily).filter(
        TriviaUserSilverModeDaily.account_id == current_user.account_id,
        TriviaUserSilverModeDaily.date == target_date,
    ).first()
    assert attempt is not None
    assert attempt.submitted_at is not None


def test_silver_mode_status_reports_winner(client, test_db, current_user):
    _add_silver_mode_config(test_db)
    _add_silver_subscription(test_db, current_user)
    target_date = get_active_draw_date()
    test_db.add(
        TriviaUserSilverModeDaily(
            account_id=current_user.account_id,
            date=target_date,
            question_id=1,
            user_answer="A",
            is_correct=True,
            submitted_at=datetime.utcnow(),
            status="answered",
        )
    )
    test_db.add(
        TriviaSilverModeWinners(
            account_id=current_user.account_id,
            draw_date=target_date,
            position=1,
            money_awarded=10.0,
            submitted_at=datetime.utcnow(),
        )
    )
    test_db.commit()

    response = client.get("/trivia/silver-mode/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_submitted"] is True
    assert payload["is_winner"] is True


def test_silver_mode_leaderboard_includes_badge(client, test_db, current_user):
    _add_silver_mode_config(test_db, badge_url="https://badge/silver.png")
    _add_silver_subscription(test_db, current_user)
    current_user.badge_id = "silver"
    test_db.commit()

    other_user = test_db.query(User).filter(User.account_id != current_user.account_id).first()
    target_date = get_active_draw_date()
    test_db.add_all(
        [
            TriviaSilverModeLeaderboard(
                account_id=current_user.account_id,
                draw_date=target_date,
                position=1,
                money_awarded=10.0,
                submitted_at=datetime.utcnow(),
            ),
            TriviaSilverModeLeaderboard(
                account_id=other_user.account_id,
                draw_date=target_date,
                position=2,
                money_awarded=5.0,
                submitted_at=datetime.utcnow(),
            ),
        ]
    )
    test_db.commit()

    response = client.get("/trivia/silver-mode/leaderboard")

    assert response.status_code == 200
    leaderboard = response.json()["leaderboard"]
    by_user = {entry["user_id"]: entry for entry in leaderboard}
    assert by_user[current_user.account_id]["badge_image_url"] == "https://badge/silver.png"


def test_silver_mode_question_requires_subscription(client, test_db):
    _add_silver_mode_config(test_db)

    response = client.get("/trivia/silver-mode/question")

    assert response.status_code == 403


def test_silver_mode_submit_answer_duplicate(client, test_db, current_user):
    _add_silver_mode_config(test_db)
    _add_silver_subscription(test_db, current_user)
    question = _create_silver_question(test_db)
    target_date = get_active_draw_date()
    start_dt, _ = get_date_range_for_query(target_date)
    test_db.add(
        TriviaQuestionsSilverModeDaily(
            date=start_dt,
            question_id=question.id,
            question_order=1,
            is_used=False,
        )
    )
    test_db.commit()

    response = client.post(
        "/trivia/silver-mode/submit-answer",
        json={"question_id": question.id, "answer": "A"},
    )
    assert response.status_code == 200

    response = client.post(
        "/trivia/silver-mode/submit-answer",
        json={"question_id": question.id, "answer": "A"},
    )
    assert response.status_code == 400


def test_silver_mode_submit_answer_closed(client, test_db, current_user, monkeypatch):
    monkeypatch.setenv("DRAW_TIMEZONE", "UTC")
    monkeypatch.setenv("DRAW_TIME_HOUR", "0")
    monkeypatch.setenv("DRAW_TIME_MINUTE", "0")

    _add_silver_mode_config(test_db)
    _add_silver_subscription(test_db, current_user)
    question = _create_silver_question(test_db)
    target_date = get_active_draw_date()
    start_dt, _ = get_date_range_for_query(target_date)
    test_db.add(
        TriviaQuestionsSilverModeDaily(
            date=start_dt,
            question_id=question.id,
            question_order=1,
            is_used=False,
        )
    )
    test_db.commit()

    response = client.post(
        "/trivia/silver-mode/submit-answer",
        json={"question_id": question.id, "answer": "A"},
    )

    assert response.status_code == 400


def test_silver_mode_leaderboard_invalid_date(client, test_db, current_user):
    _add_silver_mode_config(test_db)
    _add_silver_subscription(test_db, current_user)

    response = client.get("/trivia/silver-mode/leaderboard?draw_date=invalid")

    assert response.status_code == 400


def test_silver_mode_question_no_pool(client, test_db, current_user):
    _add_silver_mode_config(test_db)
    _add_silver_subscription(test_db, current_user)

    response = client.get("/trivia/silver-mode/question")

    assert response.status_code == 404
