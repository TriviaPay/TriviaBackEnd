"""
Subscription service for validating user access to different trivia modes.
"""
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from models import User, UserSubscription, SubscriptionPlan, TriviaModeConfig

logger = logging.getLogger(__name__)


def check_mode_access(
    db: Session,
    user: User,
    mode_id: str
) -> Dict[str, Any]:
    """
    Check if user has access to a specific mode based on subscription requirements.
    
    Args:
        db: Database session
        user: User object
        mode_id: Mode identifier
        
    Returns:
        Dictionary with:
        - has_access: bool
        - subscription_status: str
        - subscription_details: dict or None
        - message: str
    """
    # Get mode config
    from utils.trivia_mode_service import get_mode_config
    mode_config = get_mode_config(db, mode_id)
    
    if not mode_config:
        # Auto-create mode config if missing
        import json
        if mode_id == 'bronze':
            try:
                reward_distribution = {
                    "reward_type": "money",
                    "distribution_method": "harmonic_sum",
                    "requires_subscription": True,
                    "subscription_amount": 5.0,
                    "profit_share_percentage": 0.5
                }
                mode_config = TriviaModeConfig(
                    mode_id='bronze',
                    mode_name='Bronze Mode - First-Come Reward',
                    questions_count=1,
                    reward_distribution=json.dumps(reward_distribution),
                    amount=5.0,
                    leaderboard_types=json.dumps(['daily']),
                    ad_config=json.dumps({}),
                    survey_config=json.dumps({})
                )
                db.add(mode_config)
                db.commit()
                logger.info("Auto-created bronze mode config")
            except Exception as e:
                logger.error(f"Failed to auto-create bronze mode config: {str(e)}")
                return {
                    'has_access': False,
                    'subscription_status': 'mode_not_found',
                    'subscription_details': None,
                    'message': f'Mode {mode_id} not found and could not be created'
                }
        elif mode_id == 'silver':
            try:
                reward_distribution = {
                    "reward_type": "money",
                    "distribution_method": "harmonic_sum",
                    "requires_subscription": True,
                    "subscription_amount": 10.0,
                    "profit_share_percentage": 0.5
                }
                mode_config = TriviaModeConfig(
                    mode_id='silver',
                    mode_name='Silver Mode - First-Come Reward',
                    questions_count=1,
                    reward_distribution=json.dumps(reward_distribution),
                    amount=10.0,
                    leaderboard_types=json.dumps(['daily']),
                    ad_config=json.dumps({}),
                    survey_config=json.dumps({})
                )
                db.add(mode_config)
                db.commit()
                logger.info("Auto-created silver mode config")
            except Exception as e:
                logger.error(f"Failed to auto-create silver mode config: {str(e)}")
                return {
                    'has_access': False,
                    'subscription_status': 'mode_not_found',
                    'subscription_details': None,
                    'message': f'Mode {mode_id} not found and could not be created'
                }
        else:
            return {
                'has_access': False,
                'subscription_status': 'mode_not_found',
                'subscription_details': None,
                'message': f'Mode {mode_id} not found'
            }
    
    # Check if mode requires subscription
    try:
        import json
        reward_config = json.loads(mode_config.reward_distribution)
        requires_subscription = reward_config.get('requires_subscription', False)
        required_amount = reward_config.get('subscription_amount', 0.0)
    except (json.JSONDecodeError, TypeError):
        # Fallback to mode_config.amount if reward_distribution parsing fails
        requires_subscription = mode_config.amount > 0
        required_amount = mode_config.amount
    
    if not requires_subscription:
        # Mode doesn't require subscription
        return {
            'has_access': True,
            'subscription_status': 'not_required',
            'subscription_details': None,
            'message': 'Mode does not require subscription'
        }
    
    # Check for active subscription
    # Convert required_amount to cents for comparison with unit_amount_minor
    required_amount_minor = int(required_amount * 100) if required_amount > 0 else 0
    active_subscription = db.query(UserSubscription).join(SubscriptionPlan).filter(
        and_(
            UserSubscription.user_id == user.account_id,
            UserSubscription.status == 'active',
            or_(
                SubscriptionPlan.unit_amount_minor == required_amount_minor,  # Use new field (cents)
                SubscriptionPlan.price_usd == required_amount  # Fallback to deprecated field
            ),
            UserSubscription.current_period_end > datetime.utcnow()
        )
    ).first()
    
    if active_subscription:
        return {
            'has_access': True,
            'subscription_status': 'active',
            'subscription_details': {
                'subscription_id': active_subscription.id,
                'plan_id': active_subscription.plan_id,
                'current_period_end': active_subscription.current_period_end.isoformat() if active_subscription.current_period_end else None,
                'amount': required_amount
            },
            'message': 'User has active subscription'
        }
    
    # Check if subscription exists but is not active
    required_amount_minor = int(required_amount * 100) if required_amount > 0 else 0
    inactive_subscription = db.query(UserSubscription).join(SubscriptionPlan).filter(
        and_(
            UserSubscription.user_id == user.account_id,
            or_(
                SubscriptionPlan.unit_amount_minor == required_amount_minor,
                SubscriptionPlan.price_usd == required_amount
            )
        )
    ).order_by(UserSubscription.created_at.desc()).first()
    
    if inactive_subscription:
        return {
            'has_access': False,
            'subscription_status': inactive_subscription.status,
            'subscription_details': {
                'subscription_id': inactive_subscription.id,
                'plan_id': inactive_subscription.plan_id,
                'status': inactive_subscription.status,
                'current_period_end': inactive_subscription.current_period_end.isoformat() if inactive_subscription.current_period_end else None,
                'amount': required_amount
            },
            'message': f'Subscription exists but status is {inactive_subscription.status}'
        }
    
    # No subscription found
    return {
        'has_access': False,
        'subscription_status': 'no_subscription',
        'subscription_details': None,
        'message': f'No active ${required_amount} subscription found'
    }


def get_user_subscription_for_mode(
    db: Session,
    user: User,
    mode_id: str
) -> Optional[UserSubscription]:
    """
    Get user's active subscription for a specific mode.
    
    Args:
        db: Database session
        user: User object
        mode_id: Mode identifier
        
    Returns:
        UserSubscription object or None
    """
    # Get mode config
    from utils.trivia_mode_service import get_mode_config
    mode_config = get_mode_config(db, mode_id)
    
    if not mode_config:
        return None
    
    # Get required amount
    try:
        import json
        reward_config = json.loads(mode_config.reward_distribution)
        required_amount = reward_config.get('subscription_amount', 0.0)
    except (json.JSONDecodeError, TypeError):
        required_amount = mode_config.amount
    
    if required_amount == 0:
        return None
    
    # Find active subscription
    required_amount_minor = int(required_amount * 100) if required_amount > 0 else 0
    subscription = db.query(UserSubscription).join(SubscriptionPlan).filter(
        and_(
            UserSubscription.user_id == user.account_id,
            UserSubscription.status == 'active',
            or_(
                SubscriptionPlan.unit_amount_minor == required_amount_minor,
                SubscriptionPlan.price_usd == required_amount
            ),
            UserSubscription.current_period_end > datetime.utcnow()
        )
    ).first()
    
    return subscription

