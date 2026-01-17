"""
Silver Mode ($10) specific service for eligibility, ranking, and reward
distribution. All participants who submit (correct or wrong) are eligible and
ranked by submission time. Rewards distributed using harmonic sum.
"""

import calendar
import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from models import (
    SubscriptionPlan,
    TriviaModeConfig,
    TriviaSilverModeLeaderboard,
    TriviaSilverModeWinners,
    TriviaUserSilverModeDaily,
    User,
    UserSubscription,
)
from utils.mode_rewards_service import calculate_harmonic_sum_rewards

logger = logging.getLogger(__name__)


def get_eligible_participants_silver_mode(
    db: Session, draw_date: date
) -> List[Dict[str, Any]]:
    """
    Get all users who submitted an answer for silver mode on the draw date.
    All participants are eligible regardless of correctness.

    Args:
        db: Database session
        draw_date: The draw date to check

    Returns:
        List of participant dictionaries with account_id, username, and submitted_at
    """
    logger.info(f"Getting eligible participants for silver mode draw date: {draw_date}")

    # Get all users who submitted an answer for this date
    user_attempts = (
        db.query(TriviaUserSilverModeDaily)
        .filter(
            TriviaUserSilverModeDaily.date == draw_date,
            TriviaUserSilverModeDaily.submitted_at.isnot(None),  # Must have submitted
        )
        .all()
    )

    eligible_participants = []
    for attempt in user_attempts:
        # Verify user has active $10 subscription
        # Check both unit_amount_minor (1000 cents = $10) and price_usd
        # (deprecated but may still be used)
        active_subscription = (
            db.query(UserSubscription)
            .join(SubscriptionPlan)
            .filter(
                and_(
                    UserSubscription.user_id == attempt.account_id,
                    UserSubscription.status == "active",
                    or_(
                        SubscriptionPlan.unit_amount_minor == 1000,  # $10.00 in cents
                        # Fallback to deprecated field
                        SubscriptionPlan.price_usd == 10.0,
                    ),
                    UserSubscription.current_period_end > datetime.utcnow(),
                )
            )
            .first()
        )

        if not active_subscription:
            logger.warning(
                f"User {attempt.account_id} submitted but no active $10 subscription"
            )
            continue

        # Get user details
        user = db.query(User).filter(User.account_id == attempt.account_id).first()
        if user and attempt.submitted_at:
            eligible_participants.append(
                {
                    "account_id": attempt.account_id,
                    "username": user.username,
                    "submitted_at": attempt.submitted_at,
                }
            )

    logger.info(
        "Found %s eligible participants for silver mode draw on %s",
        len(eligible_participants),
        draw_date,
    )
    return eligible_participants


def rank_participants_by_submission_time(
    participants: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Rank participants by submission time (earliest first).

    Args:
        participants: List of participant dictionaries with submitted_at

    Returns:
        Ranked list of participants with position
    """
    # Sort by submission time (earliest first)
    sorted_participants = sorted(participants, key=lambda x: x["submitted_at"])

    # Add position
    ranked = []
    for i, participant in enumerate(sorted_participants, 1):
        ranked.append({**participant, "position": i})

    return ranked


def calculate_total_pool_silver_mode(
    db: Session,
    mode_config: TriviaModeConfig,
    participant_count: int,
    draw_date: date = None,
) -> float:
    """
    Calculate total prize pool for silver mode.
    Pool = active subscribers * (subscription_amount - fee_per_user)
    Apply prize_pool_share only if subscriber_count >= expenditure_offset,
    then divide by days in month for daily pool.

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
        subscription_amount = float(
            reward_dist.get("subscription_amount") or mode_config.amount or 0.0
        )
    except Exception:
        subscription_amount = float(mode_config.amount or 0.0)

    fee_per_user = float(getattr(mode_config, "fee_per_user", 0.0) or 0.0)
    net_per_user = max(subscription_amount - fee_per_user, 0.0)
    prize_pool_share = float(getattr(mode_config, "prize_pool_share", 0.005) or 0.005)
    expenditure_offset = int(getattr(mode_config, "expenditure_offset", 0) or 0)
    required_amount_minor = int(subscription_amount * 100)

    # Count active subscribers at the configured price point
    active_subscribers = (
        db.query(UserSubscription)
        .join(SubscriptionPlan)
        .filter(
            and_(
                UserSubscription.status == "active",
                or_(
                    SubscriptionPlan.unit_amount_minor == required_amount_minor,
                    SubscriptionPlan.price_usd == subscription_amount,
                ),
                UserSubscription.current_period_end > datetime.utcnow(),
            )
        )
        .count()
    )

    apply_share = active_subscribers >= expenditure_offset if expenditure_offset else True
    monthly_pool = active_subscribers * net_per_user
    monthly_prize_pool = (
        monthly_pool * prize_pool_share if apply_share else monthly_pool
    )
    if draw_date is None:
        draw_date = date.today()
    days_in_month = calendar.monthrange(draw_date.year, draw_date.month)[1]
    total_pool = monthly_prize_pool / days_in_month if days_in_month else 0.0
    logger.info(
        "Silver mode pool for %s: %s subscribers, net_per_user=%s, share=%s, offset=%s, daily_pool=$%.2f",
        draw_date,
        active_subscribers,
        net_per_user,
        prize_pool_share if apply_share else "n/a",
        expenditure_offset,
        total_pool,
    )

    return total_pool


def distribute_rewards_to_winners_silver_mode(
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
        winner_record = TriviaSilverModeWinners(
            account_id=winner["account_id"],
            draw_date=draw_date,
            position=winner["position"],
            money_awarded=reward_amount,
            submitted_at=winner["submitted_at"],
        )
        db.add(winner_record)

        # Update or create leaderboard entry
        leaderboard_entry = (
            db.query(TriviaSilverModeLeaderboard)
            .filter(
                TriviaSilverModeLeaderboard.account_id == winner["account_id"],
                TriviaSilverModeLeaderboard.draw_date == draw_date,
            )
            .first()
        )

        if leaderboard_entry:
            leaderboard_entry.position = winner["position"]
            leaderboard_entry.money_awarded = reward_amount
            leaderboard_entry.submitted_at = winner["submitted_at"]
        else:
            leaderboard_entry = TriviaSilverModeLeaderboard(
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
        "Distributed $%.2f to %s silver mode winners for %s",
        total_distributed,
        distributed_count,
        draw_date,
    )

    return {
        "status": "success",
        "winners_count": distributed_count,
        "total_distributed": total_distributed,
        "total_pool": total_pool,
    }


def cleanup_old_leaderboard_silver_mode(db: Session, previous_draw_date: date) -> int:
    """
    Clean up old leaderboard entries (keep only current day).

    Args:
        db: Database session
        previous_draw_date: Date to delete entries for

    Returns:
        Number of entries deleted
    """
    deleted_count = (
        db.query(TriviaSilverModeLeaderboard)
        .filter(TriviaSilverModeLeaderboard.draw_date == previous_draw_date)
        .delete()
    )

    db.commit()

    if deleted_count > 0:
        logger.info(
            "Cleaned up %s old silver mode leaderboard entries for %s",
            deleted_count,
            previous_draw_date,
        )

    return deleted_count
