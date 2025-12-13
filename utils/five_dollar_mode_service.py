"""
$5 Mode specific service for eligibility, ranking, and reward distribution.
All participants who submit (correct or wrong) are eligible and ranked by submission time.
Rewards distributed using harmonic sum.
"""
import json
import logging
from typing import List, Dict, Any
from datetime import date, datetime
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from models import (
    TriviaModeConfig, TriviaUserFiveDollarModeDaily, TriviaFiveDollarModeWinners,
    TriviaFiveDollarModeLeaderboard, User, UserSubscription, SubscriptionPlan
)
from utils.mode_rewards_service import calculate_harmonic_sum_rewards, rank_participants_by_time
from utils.subscription_service import check_mode_access

logger = logging.getLogger(__name__)


def get_eligible_participants_five_dollar_mode(
    db: Session,
    draw_date: date
) -> List[Dict[str, Any]]:
    """
    Get all users who submitted an answer for $5 mode on the draw date.
    All participants are eligible regardless of correctness.
    
    Args:
        db: Database session
        draw_date: The draw date to check
        
    Returns:
        List of participant dictionaries with account_id, username, and submitted_at
    """
    logger.info(f"Getting eligible participants for $5 mode draw date: {draw_date}")
    
    # Get all users who submitted an answer for this date
    user_attempts = db.query(TriviaUserFiveDollarModeDaily).filter(
        TriviaUserFiveDollarModeDaily.date == draw_date,
        TriviaUserFiveDollarModeDaily.submitted_at.isnot(None)  # Must have submitted
    ).all()
    
    eligible_participants = []
    for attempt in user_attempts:
        # Verify user has active $5 subscription
        # Check both unit_amount_minor (500 cents = $5) and price_usd (deprecated but may still be used)
        active_subscription = db.query(UserSubscription).join(SubscriptionPlan).filter(
            and_(
                UserSubscription.user_id == attempt.account_id,
                UserSubscription.status == 'active',
                or_(
                    SubscriptionPlan.unit_amount_minor == 500,  # $5.00 in cents
                    SubscriptionPlan.price_usd == 5.0  # Fallback to deprecated field
                ),
                UserSubscription.current_period_end > datetime.utcnow()
            )
        ).first()
        
        if not active_subscription:
            logger.warning(f"User {attempt.account_id} submitted but no active $5 subscription")
            continue
        
        # Get user details
        user = db.query(User).filter(User.account_id == attempt.account_id).first()
        if user and attempt.submitted_at:
            eligible_participants.append({
                'account_id': attempt.account_id,
                'username': user.username,
                'submitted_at': attempt.submitted_at
            })
    
    logger.info(f"Found {len(eligible_participants)} eligible participants for $5 mode draw on {draw_date}")
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
    return rank_participants_by_time(participants, time_field='submitted_at')


def calculate_total_pool_five_dollar_mode(
    db: Session,
    mode_config: TriviaModeConfig,
    participant_count: int
) -> float:
    """
    Calculate total prize pool for $5 mode.
    Pool = number of subscribers * $5 * profit_share_percentage
    
    Args:
        db: Database session
        mode_config: Mode configuration
        participant_count: Number of participants
        
    Returns:
        Total prize pool in USD
    """
    try:
        reward_config = json.loads(mode_config.reward_distribution)
        profit_share = reward_config.get('profit_share_percentage', 0.5)  # Default 50%
    except (json.JSONDecodeError, TypeError):
        profit_share = 0.5
    
    # Total pool = participants * $5 * profit_share
    total_pool = participant_count * 5.0 * profit_share
    
    logger.info(f"Calculated total pool for $5 mode: ${total_pool:.2f} (participants: {participant_count}, profit_share: {profit_share})")
    return total_pool


def distribute_rewards_to_winners_five_dollar_mode(
    db: Session,
    winners: List[Dict[str, Any]],
    mode_config: TriviaModeConfig,
    draw_date: date
) -> Dict[str, Any]:
    """
    Award money to winners and create winner records.
    
    Args:
        db: Database session
        winners: List of winner dictionaries with account_id, username, position, reward_amount
        mode_config: The mode configuration
        draw_date: The draw date
        
    Returns:
        Dictionary with summary of distribution
    """
    total_money_awarded = 0.0
    
    for winner in winners:
        # Update user's wallet balance
        user = db.query(User).filter(User.account_id == winner['account_id']).first()
        if user:
            money_to_award = winner.get('reward_amount', 0.0)
            # TODO: Add money to user's wallet via wallet service
            # The wallet service is async, so this would need to be handled separately
            # For now, we track the reward in the winner record
            total_money_awarded += money_to_award
        
        # Create winner record
        winner_record = TriviaFiveDollarModeWinners(
            account_id=winner['account_id'],
            draw_date=draw_date,
            position=winner['position'],
            money_awarded=winner.get('reward_amount', 0.0),
            submitted_at=winner.get('submitted_at', datetime.utcnow())
        )
        db.add(winner_record)
        
        # Create/update leaderboard entry
        leaderboard_entry = db.query(TriviaFiveDollarModeLeaderboard).filter(
            and_(
                TriviaFiveDollarModeLeaderboard.account_id == winner['account_id'],
                TriviaFiveDollarModeLeaderboard.draw_date == draw_date
            )
        ).first()
        
        if leaderboard_entry:
            leaderboard_entry.position = winner['position']
            leaderboard_entry.money_awarded = winner.get('reward_amount', 0.0)
            leaderboard_entry.submitted_at = winner.get('submitted_at', datetime.utcnow())
        else:
            leaderboard_entry = TriviaFiveDollarModeLeaderboard(
                account_id=winner['account_id'],
                draw_date=draw_date,
                position=winner['position'],
                money_awarded=winner.get('reward_amount', 0.0),
                submitted_at=winner.get('submitted_at', datetime.utcnow())
            )
            db.add(leaderboard_entry)
    
    db.commit()
    
    return {
        'total_winners': len(winners),
        'total_money_awarded': total_money_awarded
    }


def cleanup_old_leaderboard_five_dollar_mode(
    db: Session,
    previous_draw_date: date
) -> int:
    """
    Delete leaderboard entries from previous draw date.
    Called when a new draw is triggered.
    
    Args:
        db: Database session
        previous_draw_date: The draw date to clean up
        
    Returns:
        Number of entries deleted
    """
    deleted_count = db.query(TriviaFiveDollarModeLeaderboard).filter(
        TriviaFiveDollarModeLeaderboard.draw_date == previous_draw_date
    ).delete()
    
    db.commit()
    
    logger.info(f"Cleaned up {deleted_count} leaderboard entries for $5 mode draw date {previous_draw_date}")
    return deleted_count

