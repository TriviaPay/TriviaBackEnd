"""Trivia/Draws/Rewards service layer."""

from datetime import datetime, timedelta

from config import DRAW_PRIZE_POOL_CACHE_SECONDS
from utils.draw_calculations import get_next_draw_time
from utils.trivia_mode_service import get_today_in_app_timezone

from . import repository as trivia_repository

_PRIZE_POOL_CACHE = {"date": None, "value": None, "expires_at": None}
_PRIZE_POOL_TTL_SECONDS = DRAW_PRIZE_POOL_CACHE_SECONDS


def get_next_draw_with_prize_pool(db):
    next_draw_time = get_next_draw_time()

    today = get_today_in_app_timezone()
    now = datetime.utcnow()
    cached_date = _PRIZE_POOL_CACHE.get("date")
    cached_expires = _PRIZE_POOL_CACHE.get("expires_at")

    if cached_date == today and cached_expires and cached_expires > now:
        prize_pool = _PRIZE_POOL_CACHE.get("value")
    else:
        prize_pool = trivia_repository.calculate_prize_pool_for_date(db, today)
        _PRIZE_POOL_CACHE["date"] = today
        _PRIZE_POOL_CACHE["value"] = prize_pool
        _PRIZE_POOL_CACHE["expires_at"] = now + timedelta(
            seconds=_PRIZE_POOL_TTL_SECONDS
        )

    return {"next_draw_time": next_draw_time.isoformat(), "prize_pool": prize_pool}


def round_down(value: float, decimals: int = 2) -> float:
    multiplier = 10**decimals
    import math

    return math.floor(value * multiplier) / multiplier


def get_recent_winners(db, current_user):
    from utils.chat_helpers import get_user_chat_profile_data_bulk
    from utils.trivia_mode_service import (
        get_active_draw_date,
        get_today_in_app_timezone,
    )

    draw_date = trivia_repository.get_most_recent_winner_draw_date(db)
    if not draw_date:
        active_date = get_active_draw_date()
        today = get_today_in_app_timezone()
        draw_date = active_date if active_date == today else active_date

    bronze_winners = trivia_repository.get_bronze_winners_for_date(
        db, draw_date, limit=10
    )
    silver_winners = trivia_repository.get_silver_winners_for_date(
        db, draw_date, limit=10
    )

    all_user_ids = {w.account_id for w in bronze_winners} | {
        w.account_id for w in silver_winners
    }
    users = {
        u.account_id: u
        for u in trivia_repository.get_users_by_account_ids(db, all_user_ids)
    }

    profile_map = get_user_chat_profile_data_bulk(list(users.values()), db)

    result = []
    for winner in bronze_winners:
        user = users.get(winner.account_id)
        if not user:
            continue
        profile_data = profile_map.get(winner.account_id, {})
        badge_data = profile_data.get("badge") or {}
        result.append(
            {
                "mode": "bronze",
                "position": winner.position,
                "username": user.username,
                "user_id": winner.account_id,
                "money_awarded": round_down(float(winner.money_awarded), 2),
                "submitted_at": (
                    winner.submitted_at.isoformat() if winner.submitted_at else None
                ),
                "profile_pic": profile_data.get("profile_pic_url"),
                "badge_image_url": badge_data.get("image_url"),
                "avatar_url": profile_data.get("avatar_url"),
                "frame_url": profile_data.get("frame_url"),
                "subscription_badges": profile_data.get("subscription_badges", []),
                "level": profile_data.get("level", 1),
                "level_progress": profile_data.get("level_progress", "0/100"),
                "draw_date": draw_date.isoformat(),
            }
        )

    for winner in silver_winners:
        user = users.get(winner.account_id)
        if not user:
            continue
        profile_data = profile_map.get(winner.account_id, {})
        badge_data = profile_data.get("badge") or {}
        result.append(
            {
                "mode": "silver",
                "position": winner.position,
                "username": user.username,
                "user_id": winner.account_id,
                "money_awarded": round_down(float(winner.money_awarded), 2),
                "submitted_at": (
                    winner.submitted_at.isoformat() if winner.submitted_at else None
                ),
                "profile_pic": profile_data.get("profile_pic_url"),
                "badge_image_url": badge_data.get("image_url"),
                "avatar_url": profile_data.get("avatar_url"),
                "frame_url": profile_data.get("frame_url"),
                "subscription_badges": profile_data.get("subscription_badges", []),
                "level": profile_data.get("level", 1),
                "level_progress": profile_data.get("level_progress", "0/100"),
                "draw_date": draw_date.isoformat(),
            }
        )

    return {
        "draw_date": draw_date.isoformat(),
        "total_winners": len(result),
        "bronze_winners": len([w for w in result if w["mode"] == "bronze"]),
        "silver_winners": len([w for w in result if w["mode"] == "silver"]),
        "winners": result,
    }


def get_daily_login_status(db, user):
    from utils.trivia_mode_service import get_today_in_app_timezone

    today = get_today_in_app_timezone()
    week_start = today - timedelta(days=today.weekday())

    user_rewards = trivia_repository.get_user_daily_rewards_for_week(
        db, user.account_id, week_start
    )

    if not user_rewards:
        days_claimed = []
        total_gems_earned = 0
    else:
        days_claimed = []
        if user_rewards.day1_status:
            days_claimed.append(1)
        if user_rewards.day2_status:
            days_claimed.append(2)
        if user_rewards.day3_status:
            days_claimed.append(3)
        if user_rewards.day4_status:
            days_claimed.append(4)
        if user_rewards.day5_status:
            days_claimed.append(5)
        if user_rewards.day6_status:
            days_claimed.append(6)
        if user_rewards.day7_status:
            days_claimed.append(7)

        total_gems_earned = len([d for d in days_claimed if d != 7]) * 10
        if 7 in days_claimed:
            total_gems_earned += 30

    current_day = today.weekday() + 1
    days_remaining = 7 - len(days_claimed)

    return {
        "week_start_date": week_start.isoformat(),
        "current_day": current_day,
        "days_claimed": days_claimed,
        "days_remaining": days_remaining,
        "total_gems_earned_this_week": total_gems_earned,
        "day_status": {
            "monday": user_rewards.day1_status if user_rewards else False,
            "tuesday": user_rewards.day2_status if user_rewards else False,
            "wednesday": user_rewards.day3_status if user_rewards else False,
            "thursday": user_rewards.day4_status if user_rewards else False,
            "friday": user_rewards.day5_status if user_rewards else False,
            "saturday": user_rewards.day6_status if user_rewards else False,
            "sunday": user_rewards.day7_status if user_rewards else False,
        },
    }


def process_daily_login(db, user):
    from fastapi import HTTPException, status

    from utils.trivia_mode_service import get_today_in_app_timezone

    today = get_today_in_app_timezone()
    week_start = today - timedelta(days=today.weekday())

    user_rewards = trivia_repository.get_user_daily_rewards_for_week(
        db, user.account_id, week_start
    )
    if not user_rewards:
        user_rewards = trivia_repository.create_user_daily_rewards_for_week(
            db, user.account_id, week_start
        )

    day_of_week = today.weekday() + 1
    day_status_map = {
        1: user_rewards.day1_status,
        2: user_rewards.day2_status,
        3: user_rewards.day3_status,
        4: user_rewards.day4_status,
        5: user_rewards.day5_status,
        6: user_rewards.day6_status,
        7: user_rewards.day7_status,
    }

    if day_status_map[day_of_week]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Daily reward already claimed today",
        )

    gems_earned = 30 if day_of_week == 7 else 10
    user.gems += gems_earned

    if day_of_week == 1:
        user_rewards.day1_status = True
    elif day_of_week == 2:
        user_rewards.day2_status = True
    elif day_of_week == 3:
        user_rewards.day3_status = True
    elif day_of_week == 4:
        user_rewards.day4_status = True
    elif day_of_week == 5:
        user_rewards.day5_status = True
    elif day_of_week == 6:
        user_rewards.day6_status = True
    else:
        user_rewards.day7_status = True

    db.commit()
    db.refresh(user_rewards)

    return {
        "message": "Daily login reward claimed successfully",
        "gems_earned": gems_earned,
        "day_claimed": day_of_week,
        "week_start_date": week_start.isoformat(),
    }


def get_free_mode_questions(db, user):
    from utils.trivia_mode_service import get_daily_questions_for_mode

    questions = get_daily_questions_for_mode(db, "free_mode", user)
    return {"questions": questions}


def submit_free_mode_answer(db, user, question_id: int, answer: str):
    from utils.trivia_mode_service import submit_answer_for_mode

    return submit_answer_for_mode(db, "free_mode", user, question_id, answer)
