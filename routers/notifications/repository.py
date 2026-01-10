"""Notifications domain repository layer."""

from sqlalchemy.orm import Session


def get_player_by_player_id(db: Session, player_id: str):
    from models import OneSignalPlayer

    return (
        db.query(OneSignalPlayer).filter(OneSignalPlayer.player_id == player_id).first()
    )


def count_players_for_user(db: Session, user_id: int) -> int:
    from sqlalchemy import func

    from models import OneSignalPlayer

    return (
        db.query(func.count(OneSignalPlayer.id))
        .filter(OneSignalPlayer.user_id == user_id)
        .scalar()
        or 0
    )


def create_player(db: Session, *, user_id: int, player_id: str, platform: str, now):
    from models import OneSignalPlayer

    new_player = OneSignalPlayer(
        user_id=user_id,
        player_id=player_id,
        platform=platform,
        is_valid=True,
        last_active=now,
    )
    db.add(new_player)
    return new_player


def list_players_for_user(db: Session, *, user_id: int, limit: int, offset: int):
    from sqlalchemy import desc

    from models import OneSignalPlayer

    return (
        db.query(OneSignalPlayer)
        .filter(OneSignalPlayer.user_id == user_id)
        .order_by(desc(OneSignalPlayer.created_at))
        .offset(offset)
        .limit(limit)
        .all()
    )
