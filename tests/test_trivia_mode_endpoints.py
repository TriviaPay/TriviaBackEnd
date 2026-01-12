import json
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import routers.auth.profile as profile_router
import routers.auth.service as auth_service
from core.db import get_db
from main import app
from models import (
    SubscriptionPlan,
    TriviaBronzeModeLeaderboard,
    TriviaBronzeModeWinners,
    TriviaFreeModeLeaderboard,
    TriviaFreeModeWinners,
    TriviaModeConfig,
    TriviaQuestionsBronzeMode,
    TriviaQuestionsBronzeModeDaily,
    TriviaQuestionsFreeMode,
    TriviaQuestionsFreeModeDaily,
    TriviaSilverModeLeaderboard,
    TriviaUserBronzeModeDaily,
    TriviaUserFreeModeDaily,
    User,
    UserDailyRewards,
    UserSubscription,
)
from routers.dependencies import get_current_user
from utils.trivia_mode_service import get_active_draw_date, get_date_range_for_query


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


def _add_mode_config(
    test_db,
    mode_id,
    mode_name,
    questions_count,
    amount=0.0,
    reward_distribution=None,
    badge_image_url=None,
):
    reward_distribution = reward_distribution or json.dumps(
        {"requires_subscription": amount > 0, "subscription_amount": amount}
    )
    config = TriviaModeConfig(
        mode_id=mode_id,
        mode_name=mode_name,
        questions_count=questions_count,
        reward_distribution=reward_distribution,
        amount=amount,
        leaderboard_types=json.dumps(["daily"]),
        ad_config=json.dumps({}),
        survey_config=json.dumps({}),
        badge_image_url=badge_image_url,
    )
    test_db.add(config)
    test_db.commit()
    return config


def _add_subscription(test_db, user, unit_amount_minor, price_usd):
    plan = SubscriptionPlan(
        name="Test Plan",
        description="Test Plan",
        price_usd=price_usd,
        billing_interval="month",
        unit_amount_minor=unit_amount_minor,
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


def _create_free_mode_question(test_db, index):
    question = TriviaQuestionsFreeMode(
        question=f"Free question {index}",
        option_a="A",
        option_b="B",
        option_c="C",
        option_d="D",
        correct_answer="A",
        hint="hint",
        explanation="explanation",
        category="general",
        difficulty_level="easy",
        question_hash=f"free-hash-{index}",
        is_used=False,
    )
    test_db.add(question)
    test_db.flush()
    return question


def _create_bronze_mode_question(test_db, index):
    question = TriviaQuestionsBronzeMode(
        question=f"Bronze question {index}",
        option_a="A",
        option_b="B",
        option_c="C",
        option_d="D",
        correct_answer="A",
        hint="hint",
        explanation="explanation",
        category="general",
        difficulty_level="easy",
        question_hash=f"bronze-hash-{index}",
        is_used=False,
    )
    test_db.add(question)
    test_db.flush()
    return question


def _add_free_mode_daily_questions(test_db, questions, target_date):
    start_dt, _ = get_date_range_for_query(target_date)
    for order, question in enumerate(questions, 1):
        test_db.add(
            TriviaQuestionsFreeModeDaily(
                date=start_dt,
                question_id=question.id,
                question_order=order,
                is_used=False,
            )
        )
    test_db.commit()


def _add_bronze_mode_daily_question(test_db, question, target_date):
    start_dt, _ = get_date_range_for_query(target_date)
    test_db.add(
        TriviaQuestionsBronzeModeDaily(
            date=start_dt,
            question_id=question.id,
            question_order=1,
            is_used=False,
        )
    )
    test_db.commit()


def test_free_mode_questions_auto_allocates(client, test_db):
    _add_mode_config(
        test_db,
        mode_id="free_mode",
        mode_name="Free Mode",
        questions_count=3,
        amount=0.0,
        reward_distribution=json.dumps({"requires_subscription": False}),
    )
    for i in range(3):
        _create_free_mode_question(test_db, i + 1)
    test_db.commit()

    response = client.get("/trivia/free-mode/questions")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["questions"]) == 3

    target_date = get_active_draw_date()
    start_dt, end_dt = get_date_range_for_query(target_date)
    daily_count = (
        test_db.query(TriviaQuestionsFreeModeDaily)
        .filter(
            TriviaQuestionsFreeModeDaily.date >= start_dt,
            TriviaQuestionsFreeModeDaily.date <= end_dt,
        )
        .count()
    )
    assert daily_count == 3


def test_bronze_mode_question_auto_allocates(client, test_db, current_user):
    _add_mode_config(
        test_db,
        mode_id="bronze",
        mode_name="Bronze Mode",
        questions_count=1,
        amount=5.0,
        reward_distribution=json.dumps(
            {"requires_subscription": True, "subscription_amount": 5.0}
        ),
    )
    _add_subscription(test_db, current_user, unit_amount_minor=500, price_usd=5.0)
    question = _create_bronze_mode_question(test_db, 1)
    test_db.commit()

    response = client.get("/trivia/bronze-mode/question")

    assert response.status_code == 200
    payload = response.json()["question"]
    assert payload["question_id"] == question.id

    target_date = get_active_draw_date()
    start_dt, end_dt = get_date_range_for_query(target_date)
    daily_count = (
        test_db.query(TriviaQuestionsBronzeModeDaily)
        .filter(
            TriviaQuestionsBronzeModeDaily.date >= start_dt,
            TriviaQuestionsBronzeModeDaily.date <= end_dt,
        )
        .count()
    )
    assert daily_count == 1


def test_free_mode_leaderboard_uses_bulk_profile(client, test_db, current_user):
    _add_mode_config(
        test_db,
        mode_id="free_mode",
        mode_name="Free Mode",
        questions_count=3,
        amount=0.0,
        reward_distribution=json.dumps({"requires_subscription": False}),
        badge_image_url="https://badge/free.png",
    )
    _add_mode_config(
        test_db,
        mode_id="bronze",
        mode_name="Bronze Mode",
        questions_count=1,
        amount=5.0,
        reward_distribution=json.dumps(
            {"requires_subscription": True, "subscription_amount": 5.0}
        ),
        badge_image_url="https://badge/bronze.png",
    )
    _add_subscription(test_db, current_user, unit_amount_minor=500, price_usd=5.0)
    current_user.badge_id = "free_mode"

    other_user = (
        test_db.query(User).filter(User.account_id != current_user.account_id).first()
    )
    question = _create_free_mode_question(test_db, 1)
    target_date = get_active_draw_date()
    test_db.add(
        TriviaUserFreeModeDaily(
            account_id=current_user.account_id,
            date=target_date,
            question_order=1,
            question_id=question.id,
            status="answered_correct",
            is_correct=True,
            answered_at=datetime.utcnow(),
        )
    )
    test_db.add_all(
        [
            TriviaFreeModeLeaderboard(
                account_id=current_user.account_id,
                draw_date=target_date,
                position=1,
                gems_awarded=50,
                completed_at=datetime.utcnow(),
            ),
            TriviaFreeModeLeaderboard(
                account_id=other_user.account_id,
                draw_date=target_date,
                position=2,
                gems_awarded=25,
                completed_at=datetime.utcnow(),
            ),
        ]
    )
    test_db.commit()

    response = client.get("/trivia/free-mode/leaderboard")

    assert response.status_code == 200
    leaderboard = response.json()["leaderboard"]
    by_user = {entry["user_id"]: entry for entry in leaderboard}

    assert (
        by_user[current_user.account_id]["badge_image_url"] == "https://badge/free.png"
    )
    assert by_user[current_user.account_id]["subscription_badges"]
    assert by_user[current_user.account_id]["level_progress"] == "1/100"
    assert by_user[other_user.account_id]["subscription_badges"] == []


def test_bronze_mode_leaderboard_uses_bulk_profile(client, test_db, current_user):
    _add_mode_config(
        test_db,
        mode_id="bronze",
        mode_name="Bronze Mode",
        questions_count=1,
        amount=5.0,
        reward_distribution=json.dumps(
            {"requires_subscription": True, "subscription_amount": 5.0}
        ),
        badge_image_url="https://badge/bronze.png",
    )
    current_user.badge_id = "bronze"

    other_user = (
        test_db.query(User).filter(User.account_id != current_user.account_id).first()
    )
    target_date = get_active_draw_date()
    test_db.add_all(
        [
            TriviaBronzeModeLeaderboard(
                account_id=current_user.account_id,
                draw_date=target_date,
                position=1,
                money_awarded=10.0,
                submitted_at=datetime.utcnow(),
            ),
            TriviaBronzeModeLeaderboard(
                account_id=other_user.account_id,
                draw_date=target_date,
                position=2,
                money_awarded=5.0,
                submitted_at=datetime.utcnow(),
            ),
        ]
    )
    test_db.commit()

    response = client.get("/trivia/bronze-mode/leaderboard")

    assert response.status_code == 200
    leaderboard = response.json()["leaderboard"]
    by_user = {entry["user_id"]: entry for entry in leaderboard}

    assert (
        by_user[current_user.account_id]["badge_image_url"]
        == "https://badge/bronze.png"
    )
    assert by_user[current_user.account_id]["level_progress"] == "0/100"


def test_rewards_recent_winners_uses_bulk_profile(client, test_db, current_user):
    _add_mode_config(
        test_db,
        mode_id="bronze",
        mode_name="Bronze Mode",
        questions_count=1,
        amount=5.0,
        reward_distribution=json.dumps(
            {"requires_subscription": True, "subscription_amount": 5.0}
        ),
        badge_image_url="https://badge/bronze.png",
    )
    _add_mode_config(
        test_db,
        mode_id="silver",
        mode_name="Silver Mode",
        questions_count=1,
        amount=10.0,
        reward_distribution=json.dumps(
            {"requires_subscription": True, "subscription_amount": 10.0}
        ),
        badge_image_url="https://badge/silver.png",
    )
    _add_subscription(test_db, current_user, unit_amount_minor=500, price_usd=5.0)

    other_user = (
        test_db.query(User).filter(User.account_id != current_user.account_id).first()
    )
    target_date = get_active_draw_date()
    test_db.add_all(
        [
            TriviaBronzeModeLeaderboard(
                account_id=current_user.account_id,
                draw_date=target_date,
                position=1,
                money_awarded=10.0,
                submitted_at=datetime.utcnow(),
            ),
            TriviaSilverModeLeaderboard(
                account_id=other_user.account_id,
                draw_date=target_date,
                position=1,
                money_awarded=20.0,
                submitted_at=datetime.utcnow(),
            ),
        ]
    )
    test_db.commit()

    response = client.get("/rewards/recent-winners")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_winners"] == 2
    assert payload["bronze_winners"] == 1
    assert payload["silver_winners"] == 1


def test_profile_extended_update_sets_gender(client, test_db, current_user):
    response = client.post(
        "/profile/extended-update",
        json={"gender": "female"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["gender"] == "female"

    test_db.refresh(current_user)
    assert current_user.gender == "female"


def test_free_mode_current_question_returns_first_locked(client, test_db):
    _add_mode_config(
        test_db,
        mode_id="free_mode",
        mode_name="Free Mode",
        questions_count=3,
        amount=0.0,
        reward_distribution=json.dumps({"requires_subscription": False}),
    )
    questions = [_create_free_mode_question(test_db, i + 1) for i in range(3)]
    target_date = get_active_draw_date()
    _add_free_mode_daily_questions(test_db, questions, target_date)

    response = client.get("/trivia/free-mode/current-question")

    assert response.status_code == 200
    payload = response.json()["question"]
    assert payload["question_id"] == questions[0].id


def test_free_mode_submit_answer_creates_attempt(client, test_db):
    _add_mode_config(
        test_db,
        mode_id="free_mode",
        mode_name="Free Mode",
        questions_count=3,
        amount=0.0,
        reward_distribution=json.dumps({"requires_subscription": False}),
    )
    question = _create_free_mode_question(test_db, 1)
    target_date = get_active_draw_date()
    _add_free_mode_daily_questions(test_db, [question], target_date)

    response = client.post(
        "/trivia/free-mode/submit-answer",
        json={"question_id": question.id, "answer": "A"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["is_correct"] is True

    attempt = (
        test_db.query(TriviaUserFreeModeDaily)
        .filter(
            TriviaUserFreeModeDaily.account_id
            == test_db.query(User).first().account_id,
            TriviaUserFreeModeDaily.date == target_date,
            TriviaUserFreeModeDaily.question_order == 1,
        )
        .first()
    )
    assert attempt is not None
    assert attempt.status == "answered_correct"


def test_free_mode_status_completed(client, test_db, current_user):
    target_date = get_active_draw_date()
    now = datetime.utcnow()
    questions = [_create_free_mode_question(test_db, i + 1) for i in range(3)]
    attempts = []
    for order in [1, 2, 3]:
        attempts.append(
            TriviaUserFreeModeDaily(
                account_id=current_user.account_id,
                date=target_date,
                question_order=order,
                question_id=questions[order - 1].id,
                status="answered_correct",
                is_correct=True,
                answered_at=now,
                third_question_completed_at=now if order == 3 else None,
            )
        )
    test_db.add_all(attempts)
    test_db.add(
        TriviaFreeModeWinners(
            account_id=current_user.account_id,
            draw_date=target_date,
            position=1,
            gems_awarded=50,
            completed_at=now,
        )
    )
    test_db.commit()

    response = client.get("/trivia/free-mode/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["progress"]["completed"] is True
    assert payload["progress"]["all_questions_answered"] is True
    assert payload["progress"]["correct_answers"] == 3
    assert payload["is_winner"] is True
    assert payload["completion_time"] is not None


def test_free_mode_double_gems_awards_bonus(client, test_db, current_user):
    target_date = get_active_draw_date() - date.resolution
    current_user.gems = 5
    test_db.add(
        TriviaFreeModeWinners(
            account_id=current_user.account_id,
            draw_date=target_date,
            position=1,
            gems_awarded=10,
            completed_at=datetime.utcnow(),
        )
    )
    test_db.commit()

    response = client.post("/trivia/free-mode/double-gems")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["doubled_gems"] == 20
    assert payload["total_gems"] == 15

    winner = (
        test_db.query(TriviaFreeModeWinners)
        .filter(
            TriviaFreeModeWinners.account_id == current_user.account_id,
            TriviaFreeModeWinners.draw_date == target_date,
        )
        .first()
    )
    assert winner.double_gems_flag is True
    assert winner.final_gems == 20


def test_free_mode_double_gems_rejects_duplicate(client, test_db, current_user):
    target_date = get_active_draw_date() - date.resolution
    test_db.add(
        TriviaFreeModeWinners(
            account_id=current_user.account_id,
            draw_date=target_date,
            position=1,
            gems_awarded=10,
            double_gems_flag=True,
            final_gems=20,
            completed_at=datetime.utcnow(),
        )
    )
    test_db.commit()

    response = client.post("/trivia/free-mode/double-gems")

    assert response.status_code == 400


def test_free_mode_current_question_all_completed(client, test_db, current_user):
    _add_mode_config(
        test_db,
        mode_id="free_mode",
        mode_name="Free Mode",
        questions_count=3,
        amount=0.0,
        reward_distribution=json.dumps({"requires_subscription": False}),
    )
    questions = [_create_free_mode_question(test_db, i + 1) for i in range(3)]
    target_date = get_active_draw_date()
    _add_free_mode_daily_questions(test_db, questions, target_date)

    now = datetime.utcnow()
    for order, question in enumerate(questions, 1):
        test_db.add(
            TriviaUserFreeModeDaily(
                account_id=current_user.account_id,
                date=target_date,
                question_order=order,
                question_id=question.id,
                status="answered_correct",
                is_correct=True,
                answered_at=now,
            )
        )
    test_db.commit()

    response = client.get("/trivia/free-mode/current-question")

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "All questions completed"
    assert len(payload["questions"]) == 3


def test_bronze_mode_submit_answer_success(client, test_db, current_user, monkeypatch):
    monkeypatch.setenv("DRAW_TIME_HOUR", "23")
    monkeypatch.setenv("DRAW_TIME_MINUTE", "59")
    monkeypatch.setenv("DRAW_TIMEZONE", "US/Eastern")

    _add_mode_config(
        test_db,
        mode_id="bronze",
        mode_name="Bronze Mode",
        questions_count=1,
        amount=5.0,
        reward_distribution=json.dumps(
            {"requires_subscription": True, "subscription_amount": 5.0}
        ),
    )
    _add_subscription(test_db, current_user, unit_amount_minor=500, price_usd=5.0)
    question = _create_bronze_mode_question(test_db, 1)
    target_date = get_active_draw_date()
    _add_bronze_mode_daily_question(test_db, question, target_date)

    response = client.post(
        "/trivia/bronze-mode/submit-answer",
        json={"question_id": question.id, "answer": "A"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["is_correct"] is True

    attempt = (
        test_db.query(TriviaUserBronzeModeDaily)
        .filter(
            TriviaUserBronzeModeDaily.account_id == current_user.account_id,
            TriviaUserBronzeModeDaily.date == target_date,
        )
        .first()
    )
    assert attempt is not None
    assert attempt.status == "answered"


def test_bronze_mode_question_requires_subscription(client, test_db):
    _add_mode_config(
        test_db,
        mode_id="bronze",
        mode_name="Bronze Mode",
        questions_count=1,
        amount=5.0,
        reward_distribution=json.dumps(
            {"requires_subscription": True, "subscription_amount": 5.0}
        ),
    )
    question = _create_bronze_mode_question(test_db, 1)
    target_date = get_active_draw_date()
    _add_bronze_mode_daily_question(test_db, question, target_date)

    response = client.get("/trivia/bronze-mode/question")

    assert response.status_code == 403


def test_bronze_mode_submit_answer_rejects_duplicate(
    client, test_db, current_user, monkeypatch
):
    monkeypatch.setenv("DRAW_TIME_HOUR", "23")
    monkeypatch.setenv("DRAW_TIME_MINUTE", "59")
    monkeypatch.setenv("DRAW_TIMEZONE", "US/Eastern")

    _add_mode_config(
        test_db,
        mode_id="bronze",
        mode_name="Bronze Mode",
        questions_count=1,
        amount=5.0,
        reward_distribution=json.dumps(
            {"requires_subscription": True, "subscription_amount": 5.0}
        ),
    )
    _add_subscription(test_db, current_user, unit_amount_minor=500, price_usd=5.0)
    question = _create_bronze_mode_question(test_db, 1)
    target_date = get_active_draw_date()
    _add_bronze_mode_daily_question(test_db, question, target_date)
    test_db.add(
        TriviaUserBronzeModeDaily(
            account_id=current_user.account_id,
            date=target_date,
            question_id=question.id,
            user_answer="A",
            is_correct=True,
            submitted_at=datetime.utcnow(),
            status="answered",
        )
    )
    test_db.commit()

    response = client.post(
        "/trivia/bronze-mode/submit-answer",
        json={"question_id": question.id, "answer": "A"},
    )

    assert response.status_code == 400


def test_bronze_mode_status_shows_submission_and_winner(client, test_db, current_user):
    _add_mode_config(
        test_db,
        mode_id="bronze",
        mode_name="Bronze Mode",
        questions_count=1,
        amount=5.0,
        reward_distribution=json.dumps(
            {"requires_subscription": True, "subscription_amount": 5.0}
        ),
    )
    _add_subscription(test_db, current_user, unit_amount_minor=500, price_usd=5.0)
    target_date = get_active_draw_date()
    question = _create_bronze_mode_question(test_db, 1)
    test_db.add(
        TriviaUserBronzeModeDaily(
            account_id=current_user.account_id,
            date=target_date,
            question_id=question.id,
            user_answer="A",
            is_correct=True,
            submitted_at=datetime.utcnow(),
            status="answered",
        )
    )
    test_db.add(
        TriviaBronzeModeWinners(
            account_id=current_user.account_id,
            draw_date=target_date,
            position=1,
            money_awarded=10.0,
            submitted_at=datetime.utcnow(),
        )
    )
    test_db.commit()

    response = client.get("/trivia/bronze-mode/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_access"] is True
    assert payload["has_submitted"] is True
    assert payload["is_winner"] is True
    assert payload["fill_in_answer"] == "A"


def test_rewards_daily_login_flow(client, test_db, current_user):
    current_user.gems = 0
    test_db.commit()

    response = client.get("/rewards/daily-login")

    assert response.status_code == 200
    payload = response.json()
    assert payload["days_claimed"] == []

    response = client.post("/rewards/daily-login")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["total_gems"] == payload["gems_earned"]

    response = client.get("/rewards/daily-login")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["days_claimed"]) == 1


def test_rewards_daily_login_already_claimed(client, test_db, current_user):
    response = client.post("/rewards/daily-login")

    assert response.status_code == 200
    response = client.post("/rewards/daily-login")

    assert response.status_code == 400


def test_profile_gems_returns_badge_and_subscriptions(client, test_db, current_user):
    _add_mode_config(
        test_db,
        mode_id="bronze",
        mode_name="Bronze Mode",
        questions_count=1,
        amount=5.0,
        reward_distribution=json.dumps(
            {"requires_subscription": True, "subscription_amount": 5.0}
        ),
        badge_image_url="https://badge/bronze.png",
    )
    _add_subscription(test_db, current_user, unit_amount_minor=500, price_usd=5.0)
    current_user.badge_id = "bronze"
    current_user.gems = 25
    target_date = get_active_draw_date()
    test_db.add(
        TriviaBronzeModeLeaderboard(
            account_id=current_user.account_id,
            draw_date=target_date,
            position=1,
            money_awarded=12.5,
            submitted_at=datetime.utcnow(),
        )
    )
    test_db.commit()

    response = client.get("/profile/gems")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["gems"] == 25
    assert payload["badge"]["id"] == "bronze"
    assert payload["subscription_badges"]
    assert payload["recent_draw_earnings"] == 12.5


def test_profile_complete_returns_user_data(client, test_db, current_user):
    current_user.street_1 = "123 Main St"
    current_user.city = "Metropolis"
    current_user.state = "CA"
    current_user.country = "USA"
    test_db.commit()

    response = client.get("/profile/complete")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    data = payload["data"]
    assert data["username"] == current_user.username
    assert data["address"]["street_1"] == "123 Main St"
    assert data["address"]["city"] == "Metropolis"


def test_profile_change_username_updates_local_user(
    client, test_db, current_user, monkeypatch
):
    def _fake_update(*args, **kwargs):
        return None

    monkeypatch.setattr(auth_service.mgmt_client.mgmt.user, "update", _fake_update)
    current_user.username_updated = False
    test_db.commit()

    response = client.post(
        "/profile/change-username", params={"new_username": "updatedname"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True

    test_db.refresh(current_user)
    assert current_user.username == "updatedname"
    assert current_user.username_updated is True


def test_profile_change_username_requires_payment(
    client, test_db, current_user, monkeypatch
):
    def _fake_update(*args, **kwargs):
        return None

    monkeypatch.setattr(auth_service.mgmt_client.mgmt.user, "update", _fake_update)
    current_user.username_updated = True
    test_db.commit()

    response = client.post(
        "/profile/change-username", params={"new_username": "blocked"}
    )

    assert response.status_code == 403


def test_profile_send_referral_generates_code(
    client, test_db, current_user, monkeypatch
):
    monkeypatch.setattr(auth_service, "get_unique_referral_code", lambda _db: "ABCDE")
    current_user.referral_code = None
    test_db.commit()

    response = client.post("/profile/send-referral")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["referral_code"] == "ABCDE"

    test_db.refresh(current_user)
    assert current_user.referral_code == "ABCDE"


def test_profile_upload_profile_pic(client, test_db, current_user, monkeypatch):
    monkeypatch.setattr(auth_service, "AWS_PROFILE_PIC_BUCKET", "test-bucket")
    monkeypatch.setattr(auth_service, "upload_file", lambda **kwargs: True)
    monkeypatch.setattr(
        auth_service, "presign_get", lambda **kwargs: "https://cdn.test/pic.jpg"
    )
    monkeypatch.setattr(auth_service, "delete_file", lambda **kwargs: None)

    response = client.post(
        "/profile/upload-profile-pic",
        files={"file": ("pic.jpg", b"fake-image", "image/jpeg")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["profile_pic_url"] == "https://cdn.test/pic.jpg"

    test_db.refresh(current_user)
    assert current_user.profile_pic_url == "https://cdn.test/pic.jpg"


def test_profile_upload_profile_pic_rejects_type(client, monkeypatch):
    monkeypatch.setattr(auth_service, "AWS_PROFILE_PIC_BUCKET", "test-bucket")

    response = client.post(
        "/profile/upload-profile-pic",
        files={"file": ("doc.txt", b"fake", "text/plain")},
    )

    assert response.status_code == 400


def test_profile_modes_status_reflects_subscriptions(client, test_db, current_user):
    _add_mode_config(
        test_db,
        mode_id="free_mode",
        mode_name="Free Mode",
        questions_count=3,
        amount=0.0,
        reward_distribution=json.dumps({"requires_subscription": False}),
    )
    _add_mode_config(
        test_db,
        mode_id="bronze",
        mode_name="Bronze Mode",
        questions_count=1,
        amount=5.0,
        reward_distribution=json.dumps(
            {"requires_subscription": True, "subscription_amount": 5.0}
        ),
    )
    _add_mode_config(
        test_db,
        mode_id="silver",
        mode_name="Silver Mode",
        questions_count=1,
        amount=10.0,
        reward_distribution=json.dumps(
            {"requires_subscription": True, "subscription_amount": 10.0}
        ),
    )
    _add_subscription(test_db, current_user, unit_amount_minor=500, price_usd=5.0)

    response = client.get("/profile/modes/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["free_mode"]["has_access"] is True
    assert payload["bronze_mode"]["has_access"] is True
    assert payload["silver_mode"]["has_access"] is False
