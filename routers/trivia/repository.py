"""Trivia/Draws/Rewards repository layer."""

from sqlalchemy.orm import Session


def calculate_prize_pool_for_date(db: Session, target_date):
    # keep implementation centralized; heavy logic lives in rewards_logic for now
    from rewards_logic import calculate_prize_pool

    return calculate_prize_pool(db, target_date, commit_revenue=False)


def get_most_recent_winner_draw_date(db: Session):
    from sqlalchemy import func

    from models import TriviaBronzeModeLeaderboard, TriviaSilverModeLeaderboard

    bronze_max_date = db.query(func.max(TriviaBronzeModeLeaderboard.draw_date)).scalar()
    silver_max_date = db.query(func.max(TriviaSilverModeLeaderboard.draw_date)).scalar()
    if bronze_max_date and silver_max_date:
        return max(bronze_max_date, silver_max_date)
    return bronze_max_date or silver_max_date


def get_bronze_winners_for_date(db: Session, draw_date, limit: int = 10):
    from models import TriviaBronzeModeLeaderboard

    return (
        db.query(TriviaBronzeModeLeaderboard)
        .filter(TriviaBronzeModeLeaderboard.draw_date == draw_date)
        .order_by(TriviaBronzeModeLeaderboard.position)
        .limit(limit)
        .all()
    )


def get_silver_winners_for_date(db: Session, draw_date, limit: int = 10):
    from models import TriviaSilverModeLeaderboard

    return (
        db.query(TriviaSilverModeLeaderboard)
        .filter(TriviaSilverModeLeaderboard.draw_date == draw_date)
        .order_by(TriviaSilverModeLeaderboard.position)
        .limit(limit)
        .all()
    )


def get_users_by_account_ids(db: Session, account_ids):
    if not account_ids:
        return []
    from core.users import get_users_by_ids

    return get_users_by_ids(db, account_ids=list(account_ids))


def list_mode_leaderboard_entries(db: Session, *, leaderboard_model, draw_date):
    return (
        db.query(leaderboard_model)
        .filter(leaderboard_model.draw_date == draw_date)
        .order_by(leaderboard_model.position, leaderboard_model.submitted_at)
        .all()
    )


def try_advisory_lock(db: Session, *, key: int) -> bool:
    from sqlalchemy import text

    return bool(
        db.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
    )


def advisory_unlock(db: Session, *, key: int) -> None:
    from sqlalchemy import text

    db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


def get_user_daily_rewards_for_week(db: Session, account_id: int, week_start_date):
    from models import UserDailyRewards

    return (
        db.query(UserDailyRewards)
        .filter(
            UserDailyRewards.account_id == account_id,
            UserDailyRewards.week_start_date == week_start_date,
        )
        .first()
    )


def create_user_daily_rewards_for_week(db: Session, account_id: int, week_start_date):
    from models import UserDailyRewards

    rewards = UserDailyRewards(account_id=account_id, week_start_date=week_start_date)
    db.add(rewards)
    return rewards


# --- Trivia live chat ---


def get_trivia_live_chat_message_by_client_id(db: Session, *, user_id: int, draw_date, client_message_id: str):
    from models import TriviaLiveChatMessage

    return (
        db.query(TriviaLiveChatMessage)
        .filter(
            TriviaLiveChatMessage.user_id == user_id,
            TriviaLiveChatMessage.draw_date == draw_date,
            TriviaLiveChatMessage.client_message_id == client_message_id,
        )
        .first()
    )


def list_recent_trivia_live_chat_message_ids_since(db: Session, *, user_id: int, since_dt, limit: int):
    from models import TriviaLiveChatMessage

    return (
        db.query(TriviaLiveChatMessage.id)
        .filter(
            TriviaLiveChatMessage.user_id == user_id,
            TriviaLiveChatMessage.created_at >= since_dt,
        )
        .order_by(TriviaLiveChatMessage.created_at.desc())
        .limit(limit)
        .all()
    )


def count_trivia_live_chat_messages_since(db: Session, *, user_id: int, since_dt) -> int:
    from models import TriviaLiveChatMessage

    return (
        db.query(TriviaLiveChatMessage.id)
        .filter(
            TriviaLiveChatMessage.user_id == user_id,
            TriviaLiveChatMessage.created_at >= since_dt,
        )
        .count()
    )


def get_trivia_live_chat_message_for_draw(db: Session, *, message_id: int, draw_date):
    from models import TriviaLiveChatMessage

    return (
        db.query(TriviaLiveChatMessage)
        .filter(TriviaLiveChatMessage.id == message_id, TriviaLiveChatMessage.draw_date == draw_date)
        .first()
    )


def get_trivia_live_chat_viewer(db: Session, *, user_id: int, draw_date):
    from models import TriviaLiveChatViewer

    return (
        db.query(TriviaLiveChatViewer)
        .filter(TriviaLiveChatViewer.user_id == user_id, TriviaLiveChatViewer.draw_date == draw_date)
        .first()
    )


def create_trivia_live_chat_viewer(db: Session, *, user_id: int, draw_date, last_seen):
    from models import TriviaLiveChatViewer

    viewer = TriviaLiveChatViewer(user_id=user_id, draw_date=draw_date, last_seen=last_seen)
    db.add(viewer)
    return viewer


def list_trivia_live_chat_messages_in_window(db: Session, *, draw_date, window_start_utc, window_end_utc, limit: int):
    from sqlalchemy.orm import joinedload

    from models import TriviaLiveChatMessage

    return (
        db.query(TriviaLiveChatMessage)
        .options(joinedload(TriviaLiveChatMessage.user))
        .filter(
            TriviaLiveChatMessage.draw_date == draw_date,
            TriviaLiveChatMessage.created_at >= window_start_utc,
            TriviaLiveChatMessage.created_at <= window_end_utc,
        )
        .order_by(TriviaLiveChatMessage.created_at.desc())
        .limit(limit)
        .all()
    )


def list_trivia_live_chat_messages_by_ids(db: Session, *, ids):
    from sqlalchemy.orm import joinedload

    from models import TriviaLiveChatMessage

    if not ids:
        return []
    return (
        db.query(TriviaLiveChatMessage)
        .options(joinedload(TriviaLiveChatMessage.user))
        .filter(TriviaLiveChatMessage.id.in_(list(ids)))
        .all()
    )


def count_trivia_live_chat_active_viewers(db: Session, *, draw_date, cutoff_dt) -> int:
    from models import TriviaLiveChatViewer

    return (
        db.query(TriviaLiveChatViewer)
        .filter(
            TriviaLiveChatViewer.draw_date == draw_date,
            TriviaLiveChatViewer.last_seen >= cutoff_dt,
        )
        .count()
    )


def count_trivia_live_chat_session_likes(db: Session, *, draw_date) -> int:
    from models import TriviaLiveChatLike

    return (
        db.query(TriviaLiveChatLike)
        .filter(TriviaLiveChatLike.draw_date == draw_date, TriviaLiveChatLike.message_id.is_(None))
        .count()
    )


def has_trivia_live_chat_session_like(db: Session, *, user_id: int, draw_date) -> bool:
    from models import TriviaLiveChatLike

    return (
        db.query(TriviaLiveChatLike)
        .filter(
            TriviaLiveChatLike.user_id == user_id,
            TriviaLiveChatLike.draw_date == draw_date,
            TriviaLiveChatLike.message_id.is_(None),
        )
        .first()
        is not None
    )


def get_trivia_live_chat_session_like(db: Session, *, user_id: int, draw_date):
    from models import TriviaLiveChatLike

    return (
        db.query(TriviaLiveChatLike)
        .filter(
            TriviaLiveChatLike.user_id == user_id,
            TriviaLiveChatLike.draw_date == draw_date,
            TriviaLiveChatLike.message_id.is_(None),
        )
        .first()
    )


def create_trivia_live_chat_session_like(db: Session, *, user_id: int, draw_date):
    from models import TriviaLiveChatLike

    like = TriviaLiveChatLike(user_id=user_id, draw_date=draw_date, message_id=None)
    db.add(like)
    return like


# --- Free mode ---


def count_free_mode_daily_allocated(db: Session, *, start_range, end_range) -> int:
    from sqlalchemy import func

    from models import TriviaQuestionsFreeModeDaily

    return (
        db.query(func.count(TriviaQuestionsFreeModeDaily.id))
        .filter(
            TriviaQuestionsFreeModeDaily.date >= start_range,
            TriviaQuestionsFreeModeDaily.date <= end_range,
        )
        .scalar()
        or 0
    )


def count_free_mode_pool_size(db: Session) -> int:
    from sqlalchemy import func

    from models import TriviaQuestionsFreeMode

    return db.query(func.count(TriviaQuestionsFreeMode.id)).scalar() or 0


def list_free_mode_leaderboard_entries(db: Session, *, draw_date):
    from models import TriviaFreeModeLeaderboard

    return (
        db.query(TriviaFreeModeLeaderboard)
        .filter(TriviaFreeModeLeaderboard.draw_date == draw_date)
        .order_by(TriviaFreeModeLeaderboard.position, TriviaFreeModeLeaderboard.completed_at)
        .all()
    )


def get_free_mode_winner(db: Session, *, account_id: int, draw_date):
    from models import TriviaFreeModeWinners

    return (
        db.query(TriviaFreeModeWinners)
        .filter(
            TriviaFreeModeWinners.account_id == account_id,
            TriviaFreeModeWinners.draw_date == draw_date,
        )
        .first()
    )


def list_free_mode_attempts(db: Session, *, account_id: int, target_date):
    from models import TriviaUserFreeModeDaily

    return (
        db.query(TriviaUserFreeModeDaily)
        .filter(
            TriviaUserFreeModeDaily.account_id == account_id,
            TriviaUserFreeModeDaily.date == target_date,
        )
        .order_by(TriviaUserFreeModeDaily.question_order)
        .all()
    )


# --- Bronze/Silver modes ---


def get_bronze_daily_question(db: Session, *, start_datetime, end_datetime):
    from sqlalchemy.orm import joinedload

    from models import TriviaQuestionsBronzeModeDaily

    return (
        db.query(TriviaQuestionsBronzeModeDaily)
        .options(joinedload(TriviaQuestionsBronzeModeDaily.question))
        .filter(
            TriviaQuestionsBronzeModeDaily.date >= start_datetime,
            TriviaQuestionsBronzeModeDaily.date <= end_datetime,
        )
        .first()
    )


def get_random_unused_bronze_question(db: Session):
    from sqlalchemy import func

    from models import TriviaQuestionsBronzeMode

    return (
        db.query(TriviaQuestionsBronzeMode)
        .filter(TriviaQuestionsBronzeMode.is_used == False)
        .order_by(func.random())
        .first()
    )


def get_random_bronze_question(db: Session):
    from sqlalchemy import func

    from models import TriviaQuestionsBronzeMode

    return db.query(TriviaQuestionsBronzeMode).order_by(func.random()).first()


def create_bronze_daily_question(db: Session, *, start_datetime, question_id: int):
    from models import TriviaQuestionsBronzeModeDaily

    daily_question = TriviaQuestionsBronzeModeDaily(
        date=start_datetime,
        question_id=question_id,
        question_order=1,
        is_used=False,
    )
    db.add(daily_question)
    return daily_question


def mark_bronze_question_used(db: Session, *, question):
    question.is_used = True
    return question


def get_bronze_daily_question_by_id(db: Session, *, daily_id):
    from sqlalchemy.orm import joinedload

    from models import TriviaQuestionsBronzeModeDaily

    return (
        db.query(TriviaQuestionsBronzeModeDaily)
        .options(joinedload(TriviaQuestionsBronzeModeDaily.question))
        .filter(TriviaQuestionsBronzeModeDaily.id == daily_id)
        .first()
    )


def get_bronze_attempt(db: Session, *, account_id: int, target_date):
    from models import TriviaUserBronzeModeDaily

    return (
        db.query(TriviaUserBronzeModeDaily)
        .filter(
            TriviaUserBronzeModeDaily.account_id == account_id,
            TriviaUserBronzeModeDaily.date == target_date,
        )
        .first()
    )


def get_silver_daily_question(db: Session, *, start_datetime, end_datetime):
    from sqlalchemy.orm import joinedload

    from models import TriviaQuestionsSilverModeDaily

    return (
        db.query(TriviaQuestionsSilverModeDaily)
        .options(joinedload(TriviaQuestionsSilverModeDaily.question))
        .filter(
            TriviaQuestionsSilverModeDaily.date >= start_datetime,
            TriviaQuestionsSilverModeDaily.date <= end_datetime,
        )
        .first()
    )


def count_unused_silver_questions(db: Session) -> int:
    from models import TriviaQuestionsSilverMode

    return (
        db.query(TriviaQuestionsSilverMode.id)
        .filter(TriviaQuestionsSilverMode.is_used.is_(False))
        .count()
    )


def count_silver_questions(db: Session) -> int:
    from models import TriviaQuestionsSilverMode

    return db.query(TriviaQuestionsSilverMode.id).count()


def get_silver_question_by_offset(db: Session, *, offset: int):
    from models import TriviaQuestionsSilverMode

    return (
        db.query(TriviaQuestionsSilverMode)
        .order_by(TriviaQuestionsSilverMode.id)
        .offset(offset)
        .limit(1)
        .first()
    )


def get_unused_silver_question_by_offset(db: Session, *, offset: int):
    from models import TriviaQuestionsSilverMode

    return (
        db.query(TriviaQuestionsSilverMode)
        .filter(TriviaQuestionsSilverMode.is_used.is_(False))
        .order_by(TriviaQuestionsSilverMode.id)
        .offset(offset)
        .limit(1)
        .first()
    )


def create_silver_daily_question(db: Session, *, start_datetime, question_id: int):
    from models import TriviaQuestionsSilverModeDaily

    daily_question = TriviaQuestionsSilverModeDaily(
        date=start_datetime,
        question_id=question_id,
        question_order=1,
        is_used=False,
    )
    db.add(daily_question)
    return daily_question


def mark_silver_question_used(db: Session, *, question):
    question.is_used = True
    return question


def get_silver_daily_question_by_id(db: Session, *, daily_id):
    from sqlalchemy.orm import joinedload

    from models import TriviaQuestionsSilverModeDaily

    return (
        db.query(TriviaQuestionsSilverModeDaily)
        .options(joinedload(TriviaQuestionsSilverModeDaily.question))
        .filter(TriviaQuestionsSilverModeDaily.id == daily_id)
        .first()
    )


def get_silver_attempt(db: Session, *, account_id: int, target_date):
    from models import TriviaUserSilverModeDaily

    return (
        db.query(TriviaUserSilverModeDaily)
        .filter(
            TriviaUserSilverModeDaily.account_id == account_id,
            TriviaUserSilverModeDaily.date == target_date,
        )
        .first()
    )


# --- Internal ---


def get_any_free_mode_winner_for_draw(db: Session, *, draw_date):
    from models import TriviaFreeModeWinners

    return db.query(TriviaFreeModeWinners).filter(TriviaFreeModeWinners.draw_date == draw_date).first()


def get_any_bronze_winner_for_draw(db: Session, *, draw_date):
    from models import TriviaBronzeModeWinners

    return db.query(TriviaBronzeModeWinners).filter(TriviaBronzeModeWinners.draw_date == draw_date).first()


def get_any_silver_winner_for_draw(db: Session, *, draw_date):
    from models import TriviaSilverModeWinners

    return db.query(TriviaSilverModeWinners).filter(TriviaSilverModeWinners.draw_date == draw_date).first()


def list_onesignal_players_for_reminder(db: Session, *, only_incomplete_users: bool, active_draw_date):
    from sqlalchemy import select, union_all

    from models import (
        OneSignalPlayer,
        TriviaUserBronzeModeDaily,
        TriviaUserFreeModeDaily,
        TriviaUserSilverModeDaily,
    )

    base_q = db.query(OneSignalPlayer.player_id, OneSignalPlayer.user_id).filter(
        OneSignalPlayer.is_valid == True
    )
    if not only_incomplete_users:
        return base_q

    free_q = select(TriviaUserFreeModeDaily.account_id).where(
        TriviaUserFreeModeDaily.date == active_draw_date,
        TriviaUserFreeModeDaily.is_correct == True,
    )
    bronze_q = select(TriviaUserBronzeModeDaily.account_id).where(
        TriviaUserBronzeModeDaily.date == active_draw_date,
        TriviaUserBronzeModeDaily.is_correct == True,
    )
    silver_q = select(TriviaUserSilverModeDaily.account_id).where(
        TriviaUserSilverModeDaily.date == active_draw_date,
        TriviaUserSilverModeDaily.is_correct == True,
    )

    correct_user_ids_subq = union_all(free_q, bronze_q, silver_q).subquery()
    return base_q.filter(
        ~OneSignalPlayer.user_id.in_(select(correct_user_ids_subq.c.account_id))
    )


def list_valid_onesignal_players_excluding_user(db: Session, *, excluded_user_id: int):
    from models import OneSignalPlayer

    return (
        db.query(OneSignalPlayer)
        .filter(OneSignalPlayer.user_id != excluded_user_id, OneSignalPlayer.is_valid == True)
        .all()
    )


def count_rows_in_subquery(db: Session, *, subq) -> int:
    from sqlalchemy import func

    return db.query(func.count()).select_from(subq).scalar() or 0


def count_distinct_users_in_subquery(db: Session, *, subq) -> int:
    from sqlalchemy import func

    return db.query(func.count(func.distinct(subq.c.user_id))).scalar() or 0


def get_mode_attempt(db: Session, *, attempt_model, account_id: int, target_date):
    return (
        db.query(attempt_model)
        .filter(attempt_model.account_id == account_id, attempt_model.date == target_date)
        .first()
    )


def get_mode_question(db: Session, *, question_model, question_id: int):
    return db.query(question_model).filter(question_model.id == question_id).first()


def get_mode_daily_record(db: Session, *, daily_model, question_id: int, start_datetime, end_datetime):
    return (
        db.query(daily_model)
        .filter(
            daily_model.date >= start_datetime,
            daily_model.date <= end_datetime,
            daily_model.question_id == question_id,
        )
        .first()
    )


def get_bronze_winner_for_user(db: Session, *, account_id: int, draw_date):
    from models import TriviaBronzeModeWinners

    return (
        db.query(TriviaBronzeModeWinners)
        .filter(
            TriviaBronzeModeWinners.account_id == account_id,
            TriviaBronzeModeWinners.draw_date == draw_date,
        )
        .first()
    )


def get_silver_winner_for_user(db: Session, *, account_id: int, draw_date):
    from models import TriviaSilverModeWinners

    return (
        db.query(TriviaSilverModeWinners)
        .filter(
            TriviaSilverModeWinners.account_id == account_id,
            TriviaSilverModeWinners.draw_date == draw_date,
        )
        .first()
    )
