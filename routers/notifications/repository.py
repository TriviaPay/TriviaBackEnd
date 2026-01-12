"""Notifications domain repository layer."""

from datetime import datetime

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


def get_notification_counts(db: Session, *, user_id: int):
    from sqlalchemy import case, func

    from models import Notification

    total = (
        db.query(func.count(Notification.id))
        .filter(Notification.user_id == user_id)
        .scalar()
        or 0
    )
    unread = (
        db.query(func.sum(case((Notification.read == False, 1), else_=0)))
        .filter(Notification.user_id == user_id)
        .scalar()
        or 0
    )
    return total, unread


def list_notifications(
    db: Session,
    *,
    user_id: int,
    limit: int,
    offset: int,
    unread_only: bool,
    cursor,
):
    from sqlalchemy import and_, desc, or_

    from models import Notification

    query = db.query(Notification).filter(Notification.user_id == user_id)

    if unread_only:
        query = query.filter(Notification.read == False)

    if cursor:
        try:
            cursor_parts = cursor.split("|")
            cursor_time = datetime.fromisoformat(cursor_parts[0])
            cursor_id = int(cursor_parts[1]) if len(cursor_parts) > 1 else None
            if cursor_id is not None:
                query = query.filter(
                    or_(
                        Notification.created_at < cursor_time,
                        and_(
                            Notification.created_at == cursor_time,
                            Notification.id < cursor_id,
                        ),
                    )
                )
            else:
                query = query.filter(Notification.created_at < cursor_time)
        except Exception:
            pass

    if cursor:
        return (
            query.order_by(desc(Notification.created_at), desc(Notification.id))
            .limit(limit)
            .all()
        )

    return (
        query.order_by(desc(Notification.created_at), desc(Notification.id))
        .offset(offset)
        .limit(limit)
        .all()
    )


def count_notifications_for_user_by_ids(db: Session, *, user_id: int, notification_ids):
    from sqlalchemy import func

    from models import Notification

    return (
        db.query(func.count(Notification.id))
        .filter(
            Notification.id.in_(notification_ids),
            Notification.user_id == user_id,
        )
        .scalar()
        or 0
    )


def mark_notifications_read(db: Session, *, user_id: int, notification_ids, now):
    from models import Notification

    return (
        db.query(Notification)
        .filter(
            Notification.id.in_(notification_ids),
            Notification.user_id == user_id,
            Notification.read == False,
        )
        .update({Notification.read: True, Notification.read_at: now}, synchronize_session=False)
    )


def mark_all_notifications_read(db: Session, *, user_id: int, now):
    from models import Notification

    return (
        db.query(Notification)
        .filter(Notification.user_id == user_id, Notification.read == False)
        .update({Notification.read: True, Notification.read_at: now})
    )


def get_notification_for_user(db: Session, *, user_id: int, notification_id: int):
    from models import Notification

    return (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.user_id == user_id)
        .first()
    )


def delete_notifications_for_user(db: Session, *, user_id: int, read_only: bool):
    from models import Notification

    query = db.query(Notification).filter(Notification.user_id == user_id)
    if read_only:
        query = query.filter(Notification.read == True)

    deleted_count = query.count()
    query.delete(synchronize_session=False)
    return deleted_count


def get_private_chat_conversation_summary(db: Session, *, conversation_id: int):
    from models import PrivateChatConversation

    return (
        db.query(
            PrivateChatConversation.user1_id,
            PrivateChatConversation.user2_id,
            PrivateChatConversation.status,
        )
        .filter(PrivateChatConversation.id == conversation_id)
        .first()
    )
