from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from models import (
    TriviaModeConfig,
    TriviaQuestionsFreeMode,
    TriviaQuestionsFreeModeDaily,
    TriviaUserFreeModeDaily,
    User,
)
from routers.trivia.service import free_mode_current_question
from utils.trivia_mode_service import get_active_draw_date, get_date_range_for_query


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)
    tables = [
        TriviaModeConfig.__table__,
        User.__table__,
        TriviaQuestionsFreeMode.__table__,
        TriviaQuestionsFreeModeDaily.__table__,
        TriviaUserFreeModeDaily.__table__,
    ]
    for table in tables:
        table.create(bind=engine)

    db = SessionLocal()
    user = User(
        descope_user_id="test_user_1",
        email="test1@example.com",
        username="testuser1",
    )
    db.add(user)
    db.add(
        TriviaModeConfig(
            mode_id="free_mode",
            mode_name="Free Mode",
            questions_count=3,
            reward_distribution='{"requires_subscription": false}',
            amount=0.0,
            leaderboard_types='["daily"]',
            ad_config="{}",
            survey_config="{}",
        )
    )
    db.commit()

    try:
        yield db
    finally:
        db.close()
        for table in reversed(tables):
            table.drop(bind=engine)
        engine.dispose()


def _seed_free_questions(db_session):
    target_date = get_active_draw_date()
    start_datetime, _ = get_date_range_for_query(target_date)
    questions = []
    for index in range(1, 4):
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
        db_session.add(question)
        db_session.flush()
        db_session.add(
            TriviaQuestionsFreeModeDaily(
                date=start_datetime,
                question_id=question.id,
                question_order=index,
                is_used=False,
            )
        )
        questions.append(question)
    db_session.commit()
    return target_date, questions


def test_current_question_advances_past_wrong_answer(db_session, monkeypatch):
    monkeypatch.setenv("DRAW_TIME_HOUR", "23")
    monkeypatch.setenv("DRAW_TIME_MINUTE", "59")
    monkeypatch.setenv("DRAW_TIMEZONE", "US/Eastern")

    user = db_session.query(User).first()
    target_date, questions = _seed_free_questions(db_session)
    db_session.add(
        TriviaUserFreeModeDaily(
            account_id=user.account_id,
            date=target_date,
            question_order=1,
            question_id=questions[0].id,
            status="answered_wrong",
            is_correct=False,
            answered_at=datetime.utcnow(),
            ad_retry_used=False,
        )
    )
    db_session.commit()

    payload = free_mode_current_question(db_session, user=user)

    assert payload["question"]["question_id"] == questions[1].id
    assert "needs_ad_retry" not in payload


def test_current_question_returns_retry_only_after_pending_done(
    db_session, monkeypatch
):
    monkeypatch.setenv("DRAW_TIME_HOUR", "23")
    monkeypatch.setenv("DRAW_TIME_MINUTE", "59")
    monkeypatch.setenv("DRAW_TIMEZONE", "US/Eastern")

    user = db_session.query(User).first()
    target_date, questions = _seed_free_questions(db_session)
    now = datetime.utcnow()
    db_session.add_all(
        [
            TriviaUserFreeModeDaily(
                account_id=user.account_id,
                date=target_date,
                question_order=1,
                question_id=questions[0].id,
                status="answered_wrong",
                is_correct=False,
                answered_at=now,
                ad_retry_used=False,
            ),
            TriviaUserFreeModeDaily(
                account_id=user.account_id,
                date=target_date,
                question_order=2,
                question_id=questions[1].id,
                status="answered_correct",
                is_correct=True,
                answered_at=now,
            ),
            TriviaUserFreeModeDaily(
                account_id=user.account_id,
                date=target_date,
                question_order=3,
                question_id=questions[2].id,
                status="answered_correct",
                is_correct=True,
                answered_at=now,
            ),
        ]
    )
    db_session.commit()

    payload = free_mode_current_question(db_session, user=user)

    assert payload["question"]["question_id"] == questions[0].id
    assert payload["needs_ad_retry"] is True
