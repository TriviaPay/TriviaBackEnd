"""
Subscription service for validating user access to different trivia modes.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from models import SubscriptionPlan, TriviaModeConfig, User, UserSubscription

logger = logging.getLogger(__name__)


def _create_mode_config(db: Session, mode_id: str) -> Optional[TriviaModeConfig]:
    import json

    if mode_id == "bronze":
        reward_distribution = {
            "reward_type": "money",
            "distribution_method": "harmonic_sum",
            "requires_subscription": True,
            "subscription_amount": 5.0,
            "profit_share_percentage": 0.5,
        }
        mode_config = TriviaModeConfig(
            mode_id="bronze",
            mode_name="Bronze Mode - First-Come Reward",
            questions_count=1,
            reward_distribution=json.dumps(reward_distribution),
            amount=5.0,
            leaderboard_types=json.dumps(["daily"]),
            ad_config=json.dumps({}),
            survey_config=json.dumps({}),
        )
        db.add(mode_config)
        db.commit()
        logger.info("Auto-created bronze mode config")
        return mode_config
    if mode_id == "silver":
        reward_distribution = {
            "reward_type": "money",
            "distribution_method": "harmonic_sum",
            "requires_subscription": True,
            "subscription_amount": 10.0,
            "profit_share_percentage": 0.5,
        }
        mode_config = TriviaModeConfig(
            mode_id="silver",
            mode_name="Silver Mode - First-Come Reward",
            questions_count=1,
            reward_distribution=json.dumps(reward_distribution),
            amount=10.0,
            leaderboard_types=json.dumps(["daily"]),
            ad_config=json.dumps({}),
            survey_config=json.dumps({}),
        )
        db.add(mode_config)
        db.commit()
        logger.info("Auto-created silver mode config")
        return mode_config
    return None


def _ensure_mode_config(db: Session, mode_id: str) -> Optional[TriviaModeConfig]:
    from utils.trivia_mode_service import get_mode_config

    mode_config = get_mode_config(db, mode_id)
    if mode_config:
        return mode_config
    try:
        return _create_mode_config(db, mode_id)
    except Exception as e:
        logger.error(f"Failed to auto-create {mode_id} mode config: {str(e)}")
        return None


def check_mode_access(db: Session, user: User, mode_id: str) -> Dict[str, Any]:
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
    mode_config = _ensure_mode_config(db, mode_id)

    if not mode_config:
        message = f"Mode {mode_id} not found"
        if mode_id in ("bronze", "silver"):
            message = f"Mode {mode_id} not found and could not be created"
        return {
            "has_access": False,
            "subscription_status": "mode_not_found",
            "subscription_details": None,
            "message": message,
        }

    # Check if mode requires subscription
    try:
        import json

        reward_config = json.loads(mode_config.reward_distribution)
        requires_subscription = reward_config.get("requires_subscription", False)
        required_amount = reward_config.get("subscription_amount", 0.0)
    except (json.JSONDecodeError, TypeError):
        # Fallback to mode_config.amount if reward_distribution parsing fails
        requires_subscription = mode_config.amount > 0
        required_amount = mode_config.amount

    if not requires_subscription:
        # Mode doesn't require subscription
        return {
            "has_access": True,
            "subscription_status": "not_required",
            "subscription_details": None,
            "message": "Mode does not require subscription",
        }

    # Check for active subscription
    # Convert required_amount to cents for comparison with unit_amount_minor
    required_amount_minor = int(required_amount * 100) if required_amount > 0 else 0
    active_subscription = (
        db.query(UserSubscription)
        .join(SubscriptionPlan)
        .filter(
            and_(
                UserSubscription.user_id == user.account_id,
                UserSubscription.status == "active",
                or_(
                    # Use new field (cents)
                    SubscriptionPlan.unit_amount_minor == required_amount_minor,
                    # Fallback to deprecated field
                    SubscriptionPlan.price_usd == required_amount,
                ),
                UserSubscription.current_period_end > datetime.utcnow(),
            )
        )
        .first()
    )

    if active_subscription:
        return {
            "has_access": True,
            "subscription_status": "active",
            "subscription_details": {
                "subscription_id": active_subscription.id,
                "plan_id": active_subscription.plan_id,
                "current_period_end": (
                    active_subscription.current_period_end.isoformat()
                    if active_subscription.current_period_end
                    else None
                ),
                "amount": required_amount,
            },
            "message": "User has active subscription",
        }

    # Check if subscription exists but is not active
    required_amount_minor = int(required_amount * 100) if required_amount > 0 else 0
    inactive_subscription = (
        db.query(UserSubscription)
        .join(SubscriptionPlan)
        .filter(
            and_(
                UserSubscription.user_id == user.account_id,
                or_(
                    SubscriptionPlan.unit_amount_minor == required_amount_minor,
                    SubscriptionPlan.price_usd == required_amount,
                ),
            )
        )
        .order_by(UserSubscription.created_at.desc())
        .first()
    )

    if inactive_subscription:
        return {
            "has_access": False,
            "subscription_status": inactive_subscription.status,
            "subscription_details": {
                "subscription_id": inactive_subscription.id,
                "plan_id": inactive_subscription.plan_id,
                "status": inactive_subscription.status,
                "current_period_end": (
                    inactive_subscription.current_period_end.isoformat()
                    if inactive_subscription.current_period_end
                    else None
                ),
                "amount": required_amount,
            },
            "message": (
                f"Subscription exists but status is {inactive_subscription.status}"
            ),
        }

    # No subscription found
    return {
        "has_access": False,
        "subscription_status": "no_subscription",
        "subscription_details": None,
        "message": f"No active ${required_amount} subscription found",
    }


def get_user_subscription_for_mode(
    db: Session, user: User, mode_id: str
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
        required_amount = reward_config.get("subscription_amount", 0.0)
    except (json.JSONDecodeError, TypeError):
        required_amount = mode_config.amount

    if required_amount == 0:
        return None

    # Find active subscription
    required_amount_minor = int(required_amount * 100) if required_amount > 0 else 0
    subscription = (
        db.query(UserSubscription)
        .join(SubscriptionPlan)
        .filter(
            and_(
                UserSubscription.user_id == user.account_id,
                UserSubscription.status == "active",
                or_(
                    SubscriptionPlan.unit_amount_minor == required_amount_minor,
                    SubscriptionPlan.price_usd == required_amount,
                ),
                UserSubscription.current_period_end > datetime.utcnow(),
            )
        )
        .first()
    )

    return subscription


def get_modes_access_status(
    db: Session, user: User, mode_ids: List[str]
) -> Dict[str, Dict[str, Any]]:
    mode_configs = {
        config.mode_id: config
        for config in (
            db.query(TriviaModeConfig)
            .filter(TriviaModeConfig.mode_id.in_(mode_ids))
            .all()
        )
    }

    for mode_id in mode_ids:
        if mode_id not in mode_configs:
            mode_configs[mode_id] = _ensure_mode_config(db, mode_id)

    required_info = []
    for mode_id in mode_ids:
        mode_config = mode_configs.get(mode_id)
        if not mode_config:
            continue
        try:
            import json

            reward_config = json.loads(mode_config.reward_distribution)
            requires_subscription = reward_config.get("requires_subscription", False)
            required_amount = reward_config.get("subscription_amount", 0.0)
        except (json.JSONDecodeError, TypeError):
            requires_subscription = mode_config.amount > 0
            required_amount = mode_config.amount
        if requires_subscription:
            required_info.append(
                {
                    "mode_id": mode_id,
                    "required_amount": required_amount,
                    "required_amount_minor": (
                        int(required_amount * 100) if required_amount > 0 else 0
                    ),
                }
            )

    required_minors = {
        item["required_amount_minor"]
        for item in required_info
        if item["required_amount_minor"] > 0
    }
    required_amounts = {
        item["required_amount"] for item in required_info if item["required_amount"] > 0
    }

    active_map = {}
    latest_map = {}
    if required_minors or required_amounts:
        subscriptions = (
            db.query(UserSubscription, SubscriptionPlan)
            .join(SubscriptionPlan)
            .filter(
                and_(
                    UserSubscription.user_id == user.account_id,
                    or_(
                        SubscriptionPlan.unit_amount_minor.in_(
                            list(required_minors) or [0]
                        ),
                        SubscriptionPlan.price_usd.in_(list(required_amounts) or [0.0]),
                    ),
                )
            )
            .order_by(UserSubscription.created_at.desc())
            .all()
        )

        now = datetime.utcnow()
        for sub, plan in subscriptions:
            for info in required_info:
                required_minor = info["required_amount_minor"]
                required_amount = info["required_amount"]
                matches = (plan.unit_amount_minor == required_minor) or (
                    plan.price_usd == required_amount
                )
                if not matches:
                    continue
                if required_minor not in latest_map:
                    latest_map[required_minor] = sub
                if (
                    sub.status == "active"
                    and sub.current_period_end
                    and sub.current_period_end > now
                    and required_minor not in active_map
                ):
                    active_map[required_minor] = sub

    results: Dict[str, Dict[str, Any]] = {}
    for mode_id in mode_ids:
        mode_config = mode_configs.get(mode_id)
        if not mode_config:
            message = f"Mode {mode_id} not found"
            if mode_id in ("bronze", "silver"):
                message = f"Mode {mode_id} not found and could not be created"
            results[mode_id] = {
                "has_access": False,
                "subscription_status": "mode_not_found",
                "subscription_details": None,
                "message": message,
            }
            continue
        try:
            import json

            reward_config = json.loads(mode_config.reward_distribution)
            requires_subscription = reward_config.get("requires_subscription", False)
            required_amount = reward_config.get("subscription_amount", 0.0)
        except (json.JSONDecodeError, TypeError):
            requires_subscription = mode_config.amount > 0
            required_amount = mode_config.amount

        if not requires_subscription:
            results[mode_id] = {
                "has_access": True,
                "subscription_status": "not_required",
                "subscription_details": None,
                "message": "Mode does not require subscription",
            }
            continue

        required_minor = int(required_amount * 100) if required_amount > 0 else 0
        active_sub = active_map.get(required_minor)
        if active_sub:
            results[mode_id] = {
                "has_access": True,
                "subscription_status": "active",
                "subscription_details": {
                    "subscription_id": active_sub.id,
                    "plan_id": active_sub.plan_id,
                    "current_period_end": (
                        active_sub.current_period_end.isoformat()
                        if active_sub.current_period_end
                        else None
                    ),
                    "amount": required_amount,
                },
                "message": "User has active subscription",
            }
            continue

        inactive_sub = latest_map.get(required_minor)
        if inactive_sub:
            results[mode_id] = {
                "has_access": False,
                "subscription_status": inactive_sub.status,
                "subscription_details": {
                    "subscription_id": inactive_sub.id,
                    "plan_id": inactive_sub.plan_id,
                    "status": inactive_sub.status,
                    "current_period_end": (
                        inactive_sub.current_period_end.isoformat()
                        if inactive_sub.current_period_end
                        else None
                    ),
                    "amount": required_amount,
                },
                "message": f"Subscription exists but status is {inactive_sub.status}",
            }
            continue

        results[mode_id] = {
            "has_access": False,
            "subscription_status": "no_subscription",
            "subscription_details": None,
            "message": f"No active ${required_amount} subscription found",
        }

    return results
