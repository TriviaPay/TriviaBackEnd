"""
Generic mode draw service for executing draws across different trivia modes.
Uses strategy pattern to handle mode-specific logic.
"""
import logging
from typing import List, Dict, Any, Optional, Callable
from datetime import date, datetime
from sqlalchemy.orm import Session
from models import TriviaModeConfig

logger = logging.getLogger(__name__)

# Registry for mode-specific handlers
_mode_handlers = {}


def register_mode_handler(
    mode_id: str,
    eligibility_func: Callable,
    ranking_func: Callable,
    reward_calc_func: Optional[Callable] = None
):
    """
    Register mode-specific handlers for draw execution.
    
    Args:
        mode_id: Mode identifier (e.g., 'free_mode', 'five_dollar_mode')
        eligibility_func: Function to get eligible participants (db, draw_date) -> List[Dict]
        ranking_func: Function to rank participants (participants) -> List[Dict]
        reward_calc_func: Optional function to calculate total pool (db, mode_config, participant_count) -> float
    """
    _mode_handlers[mode_id] = {
        'eligibility': eligibility_func,
        'ranking': ranking_func,
        'reward_calc': reward_calc_func
    }
    logger.info(f"Registered handler for mode: {mode_id}")


def get_mode_handler(mode_id: str) -> Optional[Dict[str, Callable]]:
    """
    Get registered handler for a mode.
    
    Args:
        mode_id: Mode identifier
        
    Returns:
        Dictionary with handler functions or None if not registered
    """
    return _mode_handlers.get(mode_id)


def execute_mode_draw(
    db: Session,
    mode_id: str,
    draw_date: date,
    mode_config: Optional[TriviaModeConfig] = None
) -> Dict[str, Any]:
    """
    Execute a draw for a specific mode using registered handlers.
    
    Args:
        db: Database session
        mode_id: Mode identifier
        draw_date: Draw date to process
        mode_config: Optional mode config (will be fetched if not provided)
        
    Returns:
        Dictionary with draw results: status, draw_date, total_participants, total_winners, etc.
    """
    # Get mode config if not provided
    if mode_config is None:
        from utils.trivia_mode_service import get_mode_config
        mode_config = get_mode_config(db, mode_id)
        if not mode_config:
            logger.error(f"Mode config not found for {mode_id}")
            return {
                'status': 'error',
                'message': f'Mode config not found for {mode_id}',
                'draw_date': draw_date.isoformat()
            }
    
    # Get mode handler
    handler = get_mode_handler(mode_id)
    if not handler:
        logger.error(f"No handler registered for mode: {mode_id}")
        return {
            'status': 'error',
            'message': f'No handler registered for mode {mode_id}',
            'draw_date': draw_date.isoformat()
        }
    
    try:
        # Get eligible participants
        logger.info(f"Getting eligible participants for {mode_id} on {draw_date}")
        participants = handler['eligibility'](db, draw_date)
        
        if not participants:
            logger.info(f"No eligible participants for {mode_id} on {draw_date}")
            return {
                'status': 'no_participants',
                'draw_date': draw_date.isoformat(),
                'total_participants': 0,
                'total_winners': 0,
                'message': f'No eligible participants for draw on {draw_date}'
            }
        
        logger.info(f"Found {len(participants)} eligible participants")
        
        # Rank participants
        ranked_participants = handler['ranking'](participants)
        
        # Calculate total pool if custom function provided
        total_pool = None
        if handler.get('reward_calc'):
            total_pool = handler['reward_calc'](db, mode_config, len(ranked_participants))
        
        # Calculate reward distribution
        from utils.mode_rewards_service import calculate_reward_distribution
        reward_info = calculate_reward_distribution(mode_config, len(ranked_participants), total_pool)
        
        winner_count = reward_info['winner_count']
        reward_amounts = reward_info['reward_amounts']
        
        # Select winners
        if len(ranked_participants) <= winner_count:
            winners_list = ranked_participants
        else:
            winners_list = ranked_participants[:winner_count]
        
        # Prepare winners with reward amounts
        winners = []
        for i, participant in enumerate(winners_list):
            winner_data = {
                'account_id': participant['account_id'],
                'username': participant.get('username', 'Unknown'),
                'position': i + 1,
                'reward_amount': reward_amounts[i] if i < len(reward_amounts) else 0,
            }
            
            # Add time fields if present
            if 'submitted_at' in participant:
                winner_data['submitted_at'] = participant['submitted_at']
            if 'completed_at' in participant:
                winner_data['completed_at'] = participant['completed_at']
            if 'third_question_completed_at' in participant:
                winner_data['completed_at'] = participant['third_question_completed_at']
            
            winners.append(winner_data)
        
        return {
            'status': 'success',
            'draw_date': draw_date.isoformat(),
            'total_participants': len(ranked_participants),
            'total_winners': len(winners),
            'winners': winners,
            'reward_info': reward_info
        }
        
    except Exception as e:
        logger.error(f"Error executing draw for {mode_id}: {str(e)}", exc_info=True)
        return {
            'status': 'error',
            'draw_date': draw_date.isoformat(),
            'message': f'Error executing draw: {str(e)}'
        }

