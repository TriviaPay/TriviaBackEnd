"""
Free mode specific reward logic for winner calculation and gem distribution.
Uses generic mode_rewards_service for reward calculations.
"""
import json
import logging
from typing import List, Dict, Any
from datetime import date, datetime
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, case
from models import (
    TriviaModeConfig, TriviaUserFreeModeDaily, TriviaFreeModeWinners,
    TriviaFreeModeLeaderboard, User
)
from utils.mode_rewards_service import (
    calculate_reward_distribution as generic_calculate_reward_distribution,
    rank_participants_by_completion as generic_rank_participants_by_completion
)

logger = logging.getLogger(__name__)


def get_eligible_participants_free_mode(db: Session, draw_date: date) -> List[Dict[str, Any]]:
    """
    Get users who answered all questions correctly for free mode.
    Only users with is_correct=True AND status='answered_correct' for ALL questions are eligible.
    
    Args:
        db: Database session
        draw_date: The draw date to check
        
    Returns:
        List of participant dictionaries with account_id, username, and third_question_completed_at
    """
    logger.info(f"Getting eligible participants for free mode draw date: {draw_date}")
    
    # Get mode config to know how many questions are required
    mode_config = db.query(TriviaModeConfig).filter(
        TriviaModeConfig.mode_id == 'free_mode'
    ).first()
    
    if not mode_config:
        logger.warning("Free mode config not found")
        return []
    
    questions_count = mode_config.questions_count
    
    # Aggregate attempts per user to avoid N+1 lookups
    attempts_subq = db.query(
        TriviaUserFreeModeDaily.account_id.label("account_id"),
        func.sum(
            case(
                (
                    and_(
                        TriviaUserFreeModeDaily.is_correct.is_(True),
                        TriviaUserFreeModeDaily.status == 'answered_correct'
                    ),
                    1
                ),
                else_=0
            )
        ).label("correct_count"),
        func.max(
            case(
                (
                    TriviaUserFreeModeDaily.question_order == questions_count,
                    TriviaUserFreeModeDaily.third_question_completed_at
                ),
                else_=None
            )
        ).label("third_question_completed_at")
    ).filter(
        TriviaUserFreeModeDaily.date == draw_date
    ).group_by(
        TriviaUserFreeModeDaily.account_id
    ).subquery()
    
    rows = db.query(
        attempts_subq.c.account_id,
        User.username,
        attempts_subq.c.third_question_completed_at
    ).join(
        User, User.account_id == attempts_subq.c.account_id
    ).filter(
        attempts_subq.c.correct_count == questions_count,
        attempts_subq.c.third_question_completed_at.isnot(None)
    ).all()
    
    eligible_participants = [
        {
            'account_id': row.account_id,
            'username': row.username,
            'third_question_completed_at': row.third_question_completed_at
        }
        for row in rows
    ]
    
    logger.info(f"Found {len(eligible_participants)} eligible participants for free mode draw on {draw_date}")
    return eligible_participants


def rank_participants_by_completion(participants: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Rank participants by their 3rd question completion time (earliest first).
    Uses generic ranking function.
    
    Args:
        participants: List of participant dictionaries
        
    Returns:
        Sorted list of participants (earliest completion first)
    """
    return generic_rank_participants_by_completion(participants, completion_field='third_question_completed_at')


def calculate_reward_distribution(mode_config: TriviaModeConfig, participant_count: int) -> Dict[str, Any]:
    """
    Calculate reward distribution based on mode configuration.
    Uses generic mode_rewards_service.
    
    Args:
        mode_config: The mode configuration object
        participant_count: Number of eligible participants
        
    Returns:
        Dictionary with 'winner_count', 'gem_shares', 'total_gems_pool', and 'gem_amounts'
        (maintains backward compatibility with existing code)
    """
    # Use generic reward distribution calculator
    result = generic_calculate_reward_distribution(mode_config, participant_count)
    
    # Convert to legacy format for backward compatibility
    reward_amounts = result.get('reward_amounts', [])
    gem_amounts = [int(amount) for amount in reward_amounts]  # Convert to int for gems
    
    # Get gem shares from config for backward compatibility
    try:
        reward_config = json.loads(mode_config.reward_distribution)
        gem_shares = reward_config.get('gem_shares', [1.0])
        total_gems_pool = reward_config.get('total_gems_pool', 1000)
    except (json.JSONDecodeError, TypeError):
        gem_shares = []
        total_gems_pool = 0
    
    return {
        'winner_count': result.get('winner_count', 0),
        'gem_shares': gem_shares[:result.get('winner_count', 0)] if gem_shares else [],
        'total_gems_pool': total_gems_pool,
        'gem_amounts': gem_amounts
    }


def distribute_rewards_to_winners(
    db: Session,
    winners: List[Dict[str, Any]],
    mode_config: TriviaModeConfig,
    draw_date: date
) -> Dict[str, Any]:
    """
    Award gems to winners and create winner records.
    
    Args:
        db: Database session
        winners: List of winner dictionaries with account_id, username, position, gems_awarded
        mode_config: The mode configuration
        draw_date: The draw date
        
    Returns:
        Dictionary with summary of distribution
    """
    total_gems_awarded = 0
    
    for winner in winners:
        # Update user's gem balance
        gems_to_award = winner.get('gems_awarded', 0)
        if gems_to_award:
            updated = db.query(User).filter(
                User.account_id == winner['account_id']
            ).update(
                {User.gems: User.gems + gems_to_award},
                synchronize_session=False
            )
            if updated:
                total_gems_awarded += gems_to_award
        
        # Create winner record
        winner_record = TriviaFreeModeWinners(
            account_id=winner['account_id'],
            draw_date=draw_date,
            position=winner['position'],
            gems_awarded=winner.get('gems_awarded', 0),
            double_gems_flag=False,
            final_gems=None,
            completed_at=winner.get('completed_at', datetime.utcnow())
        )
        db.add(winner_record)
        
        # Create/update leaderboard entry
        leaderboard_entry = db.query(TriviaFreeModeLeaderboard).filter(
            and_(
                TriviaFreeModeLeaderboard.account_id == winner['account_id'],
                TriviaFreeModeLeaderboard.draw_date == draw_date
            )
        ).first()
        
        if leaderboard_entry:
            leaderboard_entry.position = winner['position']
            leaderboard_entry.gems_awarded = winner.get('gems_awarded', 0)
            leaderboard_entry.completed_at = winner.get('completed_at', datetime.utcnow())
        else:
            leaderboard_entry = TriviaFreeModeLeaderboard(
                account_id=winner['account_id'],
                draw_date=draw_date,
                position=winner['position'],
                gems_awarded=winner.get('gems_awarded', 0),
                completed_at=winner.get('completed_at', datetime.utcnow())
            )
            db.add(leaderboard_entry)
    
    db.commit()
    
    return {
        'total_winners': len(winners),
        'total_gems_awarded': total_gems_awarded
    }


def cleanup_old_leaderboard(db: Session, previous_draw_date: date) -> int:
    """
    Delete leaderboard entries from previous draw date.
    Called when a new draw is triggered.
    
    Args:
        db: Database session
        previous_draw_date: The draw date to clean up
        
    Returns:
        Number of entries deleted
    """
    deleted_count = db.query(TriviaFreeModeLeaderboard).filter(
        TriviaFreeModeLeaderboard.draw_date == previous_draw_date
    ).delete()
    
    db.commit()
    
    logger.info(f"Cleaned up {deleted_count} leaderboard entries for draw date {previous_draw_date}")
    return deleted_count
