"""
Bronze Mode ($5) specific service for eligibility, ranking, and reward distribution.
All participants who submit (correct or wrong) are eligible and ranked by submission time.
Rewards distributed using harmonic sum.
"""

import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from models import (
    SubscriptionPlan,
    TriviaBronzeModeLeaderboard,
    TriviaBronzeModeWinners,
    TriviaModeConfig,
    TriviaUserBronzeModeDaily,
    User,
    UserSubscription,
)
from utils.mode_rewards_service import (
    calculate_harmonic_sum_rewards,
    rank_participants_by_time,
)

logger = logging.getLogger(__name__)


def get_eligible_participants_bronze_mode(
    db: Session, draw_date: date
) -> List[Dict[str, Any]]:
    """
    Get all users who submitted an answer for bronze mode on the draw date.
    All participants are eligible regardless of correctness.

    Args:
        db: Database session
        draw_date: The draw date to check

    Returns:
        List of participant dictionaries with account_id, username, and submitted_at
    """
    logger.info(f"Getting eligible participants for bronze mode draw date: {draw_date}")

    # Get active $5 subscribers once, then filter attempts by that set
    active_sub_ids = (
        db.query(UserSubscription.user_id)
        .join(SubscriptionPlan)
        .filter(
            and_(
                UserSubscription.status == "active",
                or_(
                    SubscriptionPlan.unit_amount_minor == 500,  # $5.00 in cents
                    SubscriptionPlan.price_usd == 5.0,  # Fallback to deprecated field
                ),
                UserSubscription.current_period_end > datetime.utcnow(),
            )
        )
        .subquery()
    )

    rows = (
        db.query(
            TriviaUserBronzeModeDaily.account_id,
            User.username,
            TriviaUserBronzeModeDaily.submitted_at,
        )
        .join(User, User.account_id == TriviaUserBronzeModeDaily.account_id)
        .filter(
            TriviaUserBronzeModeDaily.date == draw_date,
            TriviaUserBronzeModeDaily.submitted_at.isnot(None),
            TriviaUserBronzeModeDaily.account_id.in_(active_sub_ids),
        )
        .all()
    )

    eligible_participants = [
        {
            "account_id": row.account_id,
            "username": row.username,
            "submitted_at": row.submitted_at,
        }
        for row in rows
    ]

    logger.info(
        f"Found {len(eligible_participants)} eligible participants for bronze mode draw on {draw_date}"
    )
    return eligible_participants


def rank_participants_by_submission_time(
    participants: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Rank participants by submission time (earliest first).
    Uses the generic rank_participants_by_time function.

    Args:
        participants: List of participant dictionaries

    Returns:
        Sorted list of participants (earliest submission first)
    """
    return rank_participants_by_time(participants, time_field="submitted_at")


def calculate_total_pool_bronze_mode(
    db: Session, draw_date: date, mode_config: TriviaModeConfig
) -> float:
    """
    Calculate total prize pool for bronze mode.
    Pool = number of active subscribers * $5 * profit_share_percentage

    Args:
        db: Database session
        draw_date: Draw date
        mode_config: Mode configuration

    Returns:
        Total prize pool in USD
    """
    try:
        reward_dist = (
            json.loads(mode_config.reward_distribution)
            if isinstance(mode_config.reward_distribution, str)
            else mode_config.reward_distribution
        )
        profit_share = reward_dist.get("profit_share_percentage", 0.5)  # Default 50%
    except Exception:
        profit_share = 0.5

    # Count active $5 subscribers
    active_subscribers = (
        db.query(UserSubscription)
        .join(SubscriptionPlan)
        .filter(
            and_(
                UserSubscription.status == "active",
                or_(
                    SubscriptionPlan.unit_amount_minor == 500,  # $5.00 in cents
                    SubscriptionPlan.price_usd == 5.0,
                ),
                UserSubscription.current_period_end > datetime.utcnow(),
            )
        )
        .count()
    )

    total_pool = active_subscribers * 5.0 * profit_share
    logger.info(
        f"Bronze mode pool for {draw_date}: {active_subscribers} subscribers * $5 * {profit_share} = ${total_pool:.2f}"
    )

    return total_pool


def distribute_rewards_to_winners_bronze_mode(
    db: Session, winners: List[Dict[str, Any]], draw_date: date, total_pool: float
) -> Dict[str, Any]:
    """
    Distribute rewards to winners and update leaderboard.

    Args:
        db: Database session
        winners: List of winner dictionaries with account_id, position, submitted_at
        draw_date: Draw date
        total_pool: Total prize pool

    Returns:
        Dictionary with distribution results
    """
    if not winners:
        return {"status": "no_winners", "message": "No winners to reward"}

    # Calculate rewards using harmonic sum
    rewards = calculate_harmonic_sum_rewards(len(winners), total_pool)

    distributed_count = 0
    total_distributed = 0.0

    for i, winner in enumerate(winners):
        reward_amount = rewards[i]

        # Create winner record
        winner_record = TriviaBronzeModeWinners(
            account_id=winner["account_id"],
            draw_date=draw_date,
            position=winner["position"],
            money_awarded=reward_amount,
            submitted_at=winner["submitted_at"],
        )
        db.add(winner_record)

        # Update or create leaderboard entry
        leaderboard_entry = (
            db.query(TriviaBronzeModeLeaderboard)
            .filter(
                TriviaBronzeModeLeaderboard.account_id == winner["account_id"],
                TriviaBronzeModeLeaderboard.draw_date == draw_date,
            )
            .first()
        )

        if leaderboard_entry:
            leaderboard_entry.position = winner["position"]
            leaderboard_entry.money_awarded = reward_amount
            leaderboard_entry.submitted_at = winner["submitted_at"]
        else:
            leaderboard_entry = TriviaBronzeModeLeaderboard(
                account_id=winner["account_id"],
                draw_date=draw_date,
                position=winner["position"],
                money_awarded=reward_amount,
                submitted_at=winner["submitted_at"],
            )
            db.add(leaderboard_entry)

        distributed_count += 1
        total_distributed += reward_amount

    db.commit()

    logger.info(
        f"Distributed ${total_distributed:.2f} to {distributed_count} bronze mode winners for {draw_date}"
    )

    return {
        "status": "success",
        "winners_count": distributed_count,
        "total_distributed": total_distributed,
        "total_pool": total_pool,
    }


def cleanup_old_leaderboard_bronze_mode(db: Session, previous_draw_date: date) -> int:
    """
    Clean up old leaderboard entries (keep only current day).

    Args:
        db: Database session
        previous_draw_date: Date to delete entries for

    Returns:
        Number of entries deleted
    """
    deleted_count = (
        db.query(TriviaBronzeModeLeaderboard)
        .filter(TriviaBronzeModeLeaderboard.draw_date == previous_draw_date)
        .delete()
    )

    db.commit()

    if deleted_count > 0:
        logger.info(
            f"Cleaned up {deleted_count} old bronze mode leaderboard entries for {previous_draw_date}"
        )

    return deleted_count
