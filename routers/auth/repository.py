"""Domain repository layer."""

from datetime import datetime
from typing import Optional

from sqlalchemy import and_, func, or_, select, union_all
from sqlalchemy.orm import Session

from models import (
    AdminUser,
    Avatar,
    Frame,
    SubscriptionPlan,
    TriviaBronzeModeLeaderboard,
    TriviaModeConfig,
    TriviaSilverModeLeaderboard,
    User,
    UserSubscription,
)


def get_user_by_username_ci(db: Session, username: str):
    return db.query(User).filter(func.lower(User.username) == username.lower()).first()


def get_user_by_email_ci(db: Session, email: str):
    return db.query(User).filter(func.lower(User.email) == email.lower()).first()


def get_user_by_descope_id(db: Session, descope_user_id: str):
    return db.query(User).filter(User.descope_user_id == descope_user_id).first()


def get_user_by_referral_code(db: Session, referral_code: str):
    return db.query(User).filter(User.referral_code == referral_code).first()


def get_user_by_referral_code_for_update(db: Session, referral_code: str):
    return (
        db.query(User)
        .filter(User.referral_code == referral_code)
        .with_for_update()
        .first()
    )


def get_user_by_account_id(db: Session, account_id: int):
    return db.query(User).filter(User.account_id == account_id).first()


def get_users_paginated(db: Session, skip: int, limit: int):
    return db.query(User).order_by(User.account_id).offset(skip).limit(limit).all()


def search_users(
    db: Session,
    email: Optional[str],
    username: Optional[str],
    is_admin: Optional[bool],
    contains: bool,
    skip: int,
    limit: int,
):
    query = db.query(User)
    if email:
        pattern = f"%{email}%" if contains else f"{email}%"
        query = query.filter(User.email.ilike(pattern))
    if username:
        pattern = f"%{username}%" if contains else f"{username}%"
        query = query.filter(User.username.ilike(pattern))
    if is_admin is not None:
        if is_admin:
            query = query.join(AdminUser, AdminUser.user_id == User.account_id)
        else:
            query = query.outerjoin(AdminUser, AdminUser.user_id == User.account_id).filter(
                AdminUser.user_id.is_(None)
            )
    return query.order_by(User.account_id).offset(skip).limit(limit).all()


def get_mode_config_by_id(db: Session, mode_id: str):
    return (
        db.query(TriviaModeConfig).filter(TriviaModeConfig.mode_id == mode_id).first()
    )


def get_active_subscription_prices(db: Session, user_id: int):
    return (
        db.query(SubscriptionPlan.unit_amount_minor, SubscriptionPlan.price_usd)
        .join(UserSubscription)
        .filter(
            and_(
                UserSubscription.user_id == user_id,
                UserSubscription.status == "active",
                UserSubscription.current_period_end > datetime.utcnow(),
                or_(
                    SubscriptionPlan.unit_amount_minor.in_([500, 1000]),
                    SubscriptionPlan.price_usd.in_([5.0, 10.0]),
                ),
            )
        )
        .all()
    )


def get_badges_by_mode_ids(db: Session, mode_ids):
    return (
        db.query(TriviaModeConfig)
        .filter(
            TriviaModeConfig.mode_id.in_(mode_ids),
            TriviaModeConfig.badge_image_url.isnot(None),
        )
        .all()
    )


def get_badge_by_mode_name_like(db: Session, name_pattern: str):
    return (
        db.query(TriviaModeConfig)
        .filter(
            TriviaModeConfig.mode_name.ilike(name_pattern),
            TriviaModeConfig.badge_image_url.isnot(None),
        )
        .first()
    )


def get_avatar_by_id(db: Session, avatar_id: int):
    return db.query(Avatar).filter(Avatar.id == avatar_id).first()


def get_frame_by_id(db: Session, frame_id: int):
    return db.query(Frame).filter(Frame.id == frame_id).first()


def get_recent_draw_earnings_sum(db: Session, account_id: int, draw_date):
    bronze_query = select(
        TriviaBronzeModeLeaderboard.money_awarded.label("amount")
    ).where(
        TriviaBronzeModeLeaderboard.account_id == account_id,
        TriviaBronzeModeLeaderboard.draw_date == draw_date,
    )
    silver_query = select(
        TriviaSilverModeLeaderboard.money_awarded.label("amount")
    ).where(
        TriviaSilverModeLeaderboard.account_id == account_id,
        TriviaSilverModeLeaderboard.draw_date == draw_date,
    )
    earnings_union = union_all(bronze_query, silver_query).alias("earnings")
    sum_stmt = select(func.coalesce(func.sum(earnings_union.c.amount), 0.0))
    return db.execute(sum_stmt).scalar() or 0.0
