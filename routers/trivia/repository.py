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
    from models import User

    if not account_ids:
        return []
    return db.query(User).filter(User.account_id.in_(list(account_ids))).all()


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
