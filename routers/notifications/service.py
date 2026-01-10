"""Notifications domain service layer."""

import logging
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime
from typing import Deque

from fastapi import HTTPException, status

from config import ONESIGNAL_ENABLED, ONESIGNAL_MAX_PLAYERS_PER_USER

from . import repository as notifications_repository
from .schemas import ListPlayersResponse

logger = logging.getLogger(__name__)

_rate_limit_store: "OrderedDict[str, Deque[float]]" = OrderedDict()
_rate_limit_lock = threading.Lock()
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 20
RATE_LIMIT_MAX_KEYS = 10000


def _check_rate_limit(identifier: str) -> bool:
    now = time.time()
    with _rate_limit_lock:
        bucket = _rate_limit_store.get(identifier)
        if bucket is None:
            bucket = deque()
            _rate_limit_store[identifier] = bucket
        else:
            _rate_limit_store.move_to_end(identifier)

        while bucket and now - bucket[0] >= RATE_LIMIT_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
            return False

        bucket.append(now)
        if len(_rate_limit_store) > RATE_LIMIT_MAX_KEYS:
            _rate_limit_store.popitem(last=False)

    return True


def register_onesignal_player(
    db, *, current_user, ip: str, player_id: str, platform: str
):
    if not ONESIGNAL_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="OneSignal is disabled"
        )

    rl_key = f"osreg:{ip}:{current_user.account_id}"
    if not _check_rate_limit(rl_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    if platform not in ["ios", "android", "web"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Platform must be 'ios', 'android', or 'web'",
        )

    now = datetime.utcnow()

    existing = notifications_repository.get_player_by_player_id(db, player_id)
    if existing:
        if existing.user_id != current_user.account_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Player ID is already registered to another user",
            )

        existing.last_active = now
        existing.is_valid = True
        existing.platform = platform
        try:
            db.commit()
            logger.info(
                f"Updated OneSignal player {player_id} for user {current_user.account_id}"
            )
        except Exception as exc:
            db.rollback()
            logger.error(f"Failed to update OneSignal player {player_id}: {exc}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update player",
            )

        return {
            "message": "Player updated",
            "player_id": player_id,
            "user_id": current_user.account_id,
        }

    player_count = notifications_repository.count_players_for_user(
        db, current_user.account_id
    )
    if player_count >= ONESIGNAL_MAX_PLAYERS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Player limit reached for this user",
        )

    notifications_repository.create_player(
        db,
        user_id=current_user.account_id,
        player_id=player_id,
        platform=platform,
        now=now,
    )

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(f"Failed to register OneSignal player {player_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register player",
        )

    logger.info(
        f"Registered OneSignal player {player_id} for user {current_user.account_id}"
    )
    return {
        "message": "Player registered",
        "player_id": player_id,
        "user_id": current_user.account_id,
    }


def list_onesignal_players(
    db, *, current_user, limit: int, offset: int
) -> ListPlayersResponse:
    if not ONESIGNAL_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="OneSignal is disabled"
        )

    players = notifications_repository.list_players_for_user(
        db, user_id=current_user.account_id, limit=limit, offset=offset
    )
    total = notifications_repository.count_players_for_user(db, current_user.account_id)

    return ListPlayersResponse(total=total, limit=limit, offset=offset, players=players)
