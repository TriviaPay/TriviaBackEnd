import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from models import (
    TriviaModeConfig,
    TriviaQuestionsBronzeMode,
    TriviaQuestionsBronzeModeDaily,
    TriviaQuestionsFreeMode,
    TriviaQuestionsFreeModeDaily,
    TriviaQuestionsSilverMode,
    TriviaQuestionsSilverModeDaily,
    TriviaUserBronzeModeDaily,
    TriviaUserFreeModeDaily,
    TriviaUserSilverModeDaily,
    User,
)
from routers.auth.service import upload_questions_csv
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
        User.__table__,
        TriviaModeConfig.__table__,
        TriviaQuestionsFreeMode.__table__,
        TriviaQuestionsFreeModeDaily.__table__,
        TriviaUserFreeModeDaily.__table__,
        TriviaQuestionsBronzeMode.__table__,
        TriviaQuestionsBronzeModeDaily.__table__,
        TriviaUserBronzeModeDaily.__table__,
        TriviaQuestionsSilverMode.__table__,
        TriviaQuestionsSilverModeDaily.__table__,
        TriviaUserSilverModeDaily.__table__,
    ]
    for table in tables:
        table.create(bind=engine)

    db = SessionLocal()
    db.add(
        User(
            descope_user_id="test_user_1",
            email="test1@example.com",
            username="testuser1",
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


def _add_mode_config(db_session, *, mode_id: str, questions_count: int, amount: float) -> None:
    db_session.add(
        TriviaModeConfig(
            mode_id=mode_id,
            mode_name=mode_id.replace("_", " ").title(),
            questions_count=questions_count,
            reward_distribution=json.dumps(
                {"requires_subscription": amount > 0, "subscription_amount": amount}
            ),
            amount=amount,
            leaderboard_types=json.dumps(["daily"]),
            ad_config=json.dumps({}),
            survey_config=json.dumps({}),
        )
    )
    db_session.commit()


def _create_question(db_session, *, question_model, prefix: str, index: int):
    question = question_model(
        question=f"{prefix} question {index}",
        option_a="A",
        option_b="B",
        option_c="C",
        option_d="D",
        correct_answer="A",
        hint="hint",
        explanation="explanation",
        category="general",
        difficulty_level="easy",
        question_hash=f"{prefix}-hash-{index}",
        is_used=True,
    )
    db_session.add(question)
    db_session.flush()
    return question


def _active_range():
    target_date = get_active_draw_date()
    start_datetime, end_datetime = get_date_range_for_query(target_date)
    return target_date, start_datetime, end_datetime


def _build_csv_bytes(label: str) -> bytes:
    return (
        "question,option_a,option_b,option_c,option_d,correct_answer,category,difficulty_level\n"
        f"{label},A,B,C,D,A,general,easy\n"
    ).encode("utf-8")


@pytest.mark.parametrize(
    ("mode_id", "question_model", "daily_model", "attempt_model", "daily_count", "amount"),
    [
        (
            "free_mode",
            TriviaQuestionsFreeMode,
            TriviaQuestionsFreeModeDaily,
            TriviaUserFreeModeDaily,
            3,
            0.0,
        ),
        (
            "bronze",
            TriviaQuestionsBronzeMode,
            TriviaQuestionsBronzeModeDaily,
            TriviaUserBronzeModeDaily,
            1,
            5.0,
        ),
        (
            "silver",
            TriviaQuestionsSilverMode,
            TriviaQuestionsSilverModeDaily,
            TriviaUserSilverModeDaily,
            1,
            10.0,
        ),
    ],
)
@pytest.mark.asyncio
async def test_upload_questions_clears_active_allocations_when_no_attempts(
    db_session,
    mode_id,
    question_model,
    daily_model,
    attempt_model,
    daily_count,
    amount,
):
    _add_mode_config(
        db_session,
        mode_id=mode_id,
        questions_count=daily_count,
        amount=amount,
    )
    target_date, start_datetime, end_datetime = _active_range()

    for index in range(1, daily_count + 1):
        question = _create_question(
            db_session,
            question_model=question_model,
            prefix=mode_id,
            index=index,
        )
        db_session.add(
            daily_model(
                date=start_datetime,
                question_id=question.id,
                question_order=index,
                is_used=False,
            )
        )
    db_session.commit()

    result = await upload_questions_csv(
        db_session,
        mode_id=mode_id,
        file_content=_build_csv_bytes(f"uploaded-{mode_id}"),
        max_bytes=1024 * 1024,
    )

    remaining_daily = (
        db_session.query(daily_model)
        .filter(daily_model.date >= start_datetime, daily_model.date <= end_datetime)
        .count()
    )

    assert result["success"] is True
    assert result["saved_count"] == 1
    assert result["active_draw_date"] == target_date.isoformat()
    assert result["active_pool_reset"] is True
    assert result["cleared_allocations"] == daily_count
    assert result["refresh_skipped_reason"] is None
    assert db_session.query(attempt_model).count() == 0
    assert remaining_daily == 0


@pytest.mark.parametrize(
    ("mode_id", "question_model", "daily_model", "attempt_model", "daily_count", "amount"),
    [
        (
            "free_mode",
            TriviaQuestionsFreeMode,
            TriviaQuestionsFreeModeDaily,
            TriviaUserFreeModeDaily,
            3,
            0.0,
        ),
        (
            "bronze",
            TriviaQuestionsBronzeMode,
            TriviaQuestionsBronzeModeDaily,
            TriviaUserBronzeModeDaily,
            1,
            5.0,
        ),
        (
            "silver",
            TriviaQuestionsSilverMode,
            TriviaQuestionsSilverModeDaily,
            TriviaUserSilverModeDaily,
            1,
            10.0,
        ),
    ],
)
@pytest.mark.asyncio
async def test_upload_questions_preserves_active_allocations_after_attempts(
    db_session,
    mode_id,
    question_model,
    daily_model,
    attempt_model,
    daily_count,
    amount,
):
    _add_mode_config(
        db_session,
        mode_id=mode_id,
        questions_count=daily_count,
        amount=amount,
    )
    user = db_session.query(User).first()
    target_date, start_datetime, end_datetime = _active_range()

    first_question = None
    for index in range(1, daily_count + 1):
        question = _create_question(
            db_session,
            question_model=question_model,
            prefix=f"{mode_id}-existing",
            index=index,
        )
        if first_question is None:
            first_question = question
        db_session.add(
            daily_model(
                date=start_datetime,
                question_id=question.id,
                question_order=index,
                is_used=False,
            )
        )
    db_session.flush()

    if mode_id == "free_mode":
        db_session.add(
            attempt_model(
                account_id=user.account_id,
                date=target_date,
                question_order=1,
                question_id=first_question.id,
                user_answer="a",
                is_correct=True,
                status="answered_correct",
            )
        )
    else:
        db_session.add(
            attempt_model(
                account_id=user.account_id,
                date=target_date,
                question_id=first_question.id,
                user_answer="a",
                is_correct=True,
                status="answered",
            )
        )
    db_session.commit()

    result = await upload_questions_csv(
        db_session,
        mode_id=mode_id,
        file_content=_build_csv_bytes(f"uploaded-{mode_id}-with-attempt"),
        max_bytes=1024 * 1024,
    )

    remaining_daily = (
        db_session.query(daily_model)
        .filter(daily_model.date >= start_datetime, daily_model.date <= end_datetime)
        .count()
    )

    assert result["success"] is True
    assert result["saved_count"] == 1
    assert result["active_draw_date"] == target_date.isoformat()
    assert result["active_pool_reset"] is False
    assert result["cleared_allocations"] == 0
    assert result["refresh_skipped_reason"] == "attempts_exist"
    assert remaining_daily == daily_count
