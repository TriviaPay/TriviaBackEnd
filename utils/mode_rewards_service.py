"""
Generic mode rewards service for calculating and distributing rewards across different trivia modes.
Supports multiple distribution methods: harmonic sum, tiered, and fixed.
"""
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import date, datetime
from sqlalchemy.orm import Session
from models import TriviaModeConfig, User

logger = logging.getLogger(__name__)


def calculate_harmonic_sum_rewards(participant_count: int, total_pool: float) -> List[float]:
    """
    Calculate rewards using harmonic sum distribution.
    Each participant at position i gets: (1/i) / H(n) * total_pool
    where H(n) = 1 + 1/2 + 1/3 + ... + 1/n (harmonic series)
    
    Earlier submissions (lower position) get larger shares.
    
    Args:
        participant_count: Number of participants
        total_pool: Total reward pool amount
        
    Returns:
        List of reward amounts for each participant (position 0 = first, position 1 = second, etc.)
    """
    if participant_count == 0:
        return []
    
    if participant_count == 1:
        return [total_pool]
    
    # Calculate harmonic series sum: H(n) = 1 + 1/2 + 1/3 + ... + 1/n
    harmonic_sum = sum(1.0 / i for i in range(1, participant_count + 1))
    
    # Calculate each participant's share: (1/position) / H(n) * total_pool
    # Position 1 (first) gets 1/1, position 2 gets 1/2, etc.
    rewards = []
    for i in range(1, participant_count + 1):
        share = (1.0 / i) / harmonic_sum * total_pool
        rewards.append(share)
    
    return rewards


def calculate_tiered_rewards(
    participant_count: int,
    tiered_config: Dict[str, int],
    shares: List[float],
    total_pool: float
) -> List[float]:
    """
    Calculate rewards using tiered distribution.
    
    Args:
        participant_count: Number of eligible participants
        tiered_config: Dictionary mapping thresholds to winner counts (e.g., {"default": 10, "<50": 5})
        shares: List of share percentages/weights for each tier
        total_pool: Total reward pool amount
        
    Returns:
        List of reward amounts for each winner
    """
    # Determine winner count based on tiered config
    winner_count = 1  # default
    
    for threshold, count in tiered_config.items():
        if threshold == 'default':
            winner_count = count
        else:
            # Parse threshold like "<50" or ">100"
            threshold_num = int(threshold.replace('<', '').replace('>', ''))
            if threshold.startswith('<') and participant_count < threshold_num:
                winner_count = count
                break
            elif threshold.startswith('>') and participant_count > threshold_num:
                winner_count = count
                break
    
    # Cap winner count at participant count
    winner_count = min(winner_count, participant_count)
    
    if winner_count == 0:
        return []
    
    # Calculate rewards based on shares
    rewards = []
    if shares and len(shares) > 0:
        # Normalize shares
        total_share = sum(shares[:winner_count])
        if total_share > 0:
            for i in range(winner_count):
                share = shares[i] if i < len(shares) else shares[-1] / (i + 1)
                reward = (share / total_share) * total_pool
                rewards.append(reward)
        else:
            # Equal distribution if no valid shares
            reward_per_winner = total_pool / winner_count
            rewards = [reward_per_winner] * winner_count
    else:
        # Equal distribution if no shares specified
        reward_per_winner = total_pool / winner_count
        rewards = [reward_per_winner] * winner_count
    
    return rewards


def calculate_fixed_rewards(
    fixed_winner_count: int,
    participant_count: int,
    shares: List[float],
    total_pool: float
) -> List[float]:
    """
    Calculate rewards using fixed winner count.
    
    Args:
        fixed_winner_count: Fixed number of winners
        participant_count: Number of eligible participants
        shares: List of share percentages/weights for each winner
        total_pool: Total reward pool amount
        
    Returns:
        List of reward amounts for each winner
    """
    winner_count = min(fixed_winner_count, participant_count)
    
    if winner_count == 0:
        return []
    
    # Calculate rewards based on shares
    rewards = []
    if shares and len(shares) > 0:
        total_share = sum(shares[:winner_count])
        if total_share > 0:
            for i in range(winner_count):
                share = shares[i] if i < len(shares) else shares[-1] / (i + 1)
                reward = (share / total_share) * total_pool
                rewards.append(reward)
        else:
            reward_per_winner = total_pool / winner_count
            rewards = [reward_per_winner] * winner_count
    else:
        reward_per_winner = total_pool / winner_count
        rewards = [reward_per_winner] * winner_count
    
    return rewards


def calculate_reward_distribution(
    mode_config: TriviaModeConfig,
    participant_count: int,
    total_pool: Optional[float] = None
) -> Dict[str, Any]:
    """
    Calculate reward distribution based on mode configuration.
    Supports harmonic sum, tiered, and fixed distribution methods.
    
    Args:
        mode_config: The mode configuration object
        participant_count: Number of eligible participants
        total_pool: Optional override for total pool (if None, uses config value)
        
    Returns:
        Dictionary with 'winner_count', 'reward_amounts', 'total_pool', and 'distribution_method'
    """
    try:
        reward_config = json.loads(mode_config.reward_distribution)
    except (json.JSONDecodeError, TypeError):
        logger.error(f"Invalid reward_distribution JSON for mode {mode_config.mode_id}")
        return {
            'winner_count': 0,
            'reward_amounts': [],
            'total_pool': 0,
            'distribution_method': 'unknown'
        }
    
    # Get distribution method
    distribution_method = reward_config.get('distribution_method', 'tiered')
    
    # Get total pool
    if total_pool is None:
        # Check if it's money or gems
        reward_type = reward_config.get('reward_type', 'gems')
        if reward_type == 'money':
            total_pool = reward_config.get('total_money_pool', 0.0)
        else:
            total_pool = float(reward_config.get('total_gems_pool', 1000))
    
    reward_amounts = []
    winner_count = 0
    
    if distribution_method == 'harmonic_sum':
        # Harmonic sum distribution - all participants get rewards
        winner_count = participant_count
        reward_amounts = calculate_harmonic_sum_rewards(participant_count, total_pool)
        
    elif distribution_method == 'tiered':
        # Tiered distribution
        tiered_config = reward_config.get('tiered_config', {'default': 1})
        shares = reward_config.get('shares', [1.0])
        reward_amounts = calculate_tiered_rewards(participant_count, tiered_config, shares, total_pool)
        winner_count = len(reward_amounts)
        
    elif distribution_method == 'fixed':
        # Fixed winner count
        fixed_winner_count = reward_config.get('fixed_winner_count', 1)
        shares = reward_config.get('shares', [1.0])
        reward_amounts = calculate_fixed_rewards(fixed_winner_count, participant_count, shares, total_pool)
        winner_count = len(reward_amounts)
        
    else:
        logger.warning(f"Unknown distribution method: {distribution_method}, defaulting to tiered")
        tiered_config = reward_config.get('tiered_config', {'default': 1})
        shares = reward_config.get('shares', [1.0])
        reward_amounts = calculate_tiered_rewards(participant_count, tiered_config, shares, total_pool)
        winner_count = len(reward_amounts)
    
    return {
        'winner_count': winner_count,
        'reward_amounts': reward_amounts,
        'total_pool': total_pool,
        'distribution_method': distribution_method
    }


def rank_participants_by_time(
    participants: List[Dict[str, Any]],
    time_field: str = 'submitted_at'
) -> List[Dict[str, Any]]:
    """
    Rank participants by submission time (earliest first).
    
    Args:
        participants: List of participant dictionaries
        time_field: Field name containing the timestamp (e.g., 'submitted_at', 'completed_at')
        
    Returns:
        Sorted list of participants (earliest first)
    """
    return sorted(
        participants,
        key=lambda x: x.get(time_field, datetime.min)
    )


def rank_participants_by_completion(
    participants: List[Dict[str, Any]],
    completion_field: str = 'third_question_completed_at'
) -> List[Dict[str, Any]]:
    """
    Rank participants by completion time (earliest first).
    
    Args:
        participants: List of participant dictionaries
        completion_field: Field name containing the completion timestamp
        
    Returns:
        Sorted list of participants (earliest completion first)
    """
    return sorted(
        participants,
        key=lambda x: x.get(completion_field, datetime.min)
    )


def distribute_rewards_generic(
    db: Session,
    winners: List[Dict[str, Any]],
    mode_id: str,
    draw_date: date,
    reward_type: str = 'gems'
) -> Dict[str, Any]:
    """
    Generic reward distribution function that routes to mode-specific handlers.
    
    Args:
        db: Database session
        winners: List of winner dictionaries
        mode_id: Mode identifier
        draw_date: Draw date
        reward_type: 'gems' or 'money'
        
    Returns:
        Dictionary with distribution summary
    """
    if mode_id == 'free_mode':
        from utils.free_mode_rewards import distribute_rewards_to_winners
        from models import TriviaModeConfig
        mode_config = db.query(TriviaModeConfig).filter(
            TriviaModeConfig.mode_id == mode_id
        ).first()
        if mode_config:
            return distribute_rewards_to_winners(db, winners, mode_config, draw_date)
    elif mode_id == 'five_dollar_mode':
        from utils.five_dollar_mode_service import distribute_rewards_to_winners_five_dollar_mode
        from models import TriviaModeConfig
        mode_config = db.query(TriviaModeConfig).filter(
            TriviaModeConfig.mode_id == mode_id
        ).first()
        if mode_config:
            return distribute_rewards_to_winners_five_dollar_mode(db, winners, mode_config, draw_date)
    
    return {
        'total_winners': len(winners),
        'total_rewarded': 0
    }

