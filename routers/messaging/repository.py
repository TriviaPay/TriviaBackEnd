"""Messaging/Realtime repository layer."""

from sqlalchemy.orm import Session


def query(db: Session, *entities):
    return db.query(*entities)


def get_conversation_if_participant(db: Session, *, conversation_id, user_id: int):
    from models import DMConversation, DMParticipant

    return (
        db.query(DMConversation)
        .join(DMParticipant, DMConversation.id == DMParticipant.conversation_id)
        .filter(DMConversation.id == conversation_id, DMParticipant.user_id == user_id)
        .first()
    )


def get_participant(db: Session, *, conversation_id, user_id: int):
    from models import DMParticipant

    return (
        db.query(DMParticipant)
        .filter(
            DMParticipant.conversation_id == conversation_id,
            DMParticipant.user_id == user_id,
        )
        .first()
    )


def list_participants(db: Session, *, conversation_id):
    from models import DMParticipant

    return (
        db.query(DMParticipant)
        .filter(DMParticipant.conversation_id == conversation_id)
        .all()
    )


def get_active_device_for_user(db: Session, *, user_id: int):
    from models import E2EEDevice

    return (
        db.query(E2EEDevice)
        .filter(E2EEDevice.user_id == user_id, E2EEDevice.status == "active")
        .first()
    )


def has_revoked_device(db: Session, *, user_id: int) -> bool:
    from models import E2EEDevice

    revoked = (
        db.query(E2EEDevice)
        .filter(E2EEDevice.user_id == user_id, E2EEDevice.status == "revoked")
        .first()
    )
    return revoked is not None


def get_existing_message_by_client_id(
    db: Session, *, conversation_id, sender_user_id: int, client_message_id: str
):
    from models import DMMessage

    return (
        db.query(DMMessage)
        .filter(
            DMMessage.conversation_id == conversation_id,
            DMMessage.sender_user_id == sender_user_id,
            DMMessage.client_message_id == client_message_id,
        )
        .first()
    )


def list_recent_sent_message_ids(
    db: Session, *, sender_user_id: int, since_dt, limit: int
):
    from models import DMMessage

    return (
        db.query(DMMessage.id)
        .filter(
            DMMessage.sender_user_id == sender_user_id, DMMessage.created_at >= since_dt
        )
        .order_by(DMMessage.created_at.desc())
        .limit(limit)
        .all()
    )


def get_oldest_sent_message_since(db: Session, *, sender_user_id: int, since_dt):
    from models import DMMessage

    return (
        db.query(DMMessage)
        .filter(
            DMMessage.sender_user_id == sender_user_id, DMMessage.created_at >= since_dt
        )
        .order_by(DMMessage.created_at.asc())
        .first()
    )


def list_recent_conversation_message_ids(
    db: Session, *, conversation_id, sender_user_id: int, since_dt, limit: int
):
    from models import DMMessage

    return (
        db.query(DMMessage.id)
        .filter(
            DMMessage.sender_user_id == sender_user_id,
            DMMessage.conversation_id == conversation_id,
            DMMessage.created_at >= since_dt,
        )
        .order_by(DMMessage.created_at.desc())
        .limit(limit)
        .all()
    )


def get_oldest_conversation_message_since(
    db: Session, *, conversation_id, sender_user_id: int, since_dt
):
    from models import DMMessage

    return (
        db.query(DMMessage)
        .filter(
            DMMessage.sender_user_id == sender_user_id,
            DMMessage.conversation_id == conversation_id,
            DMMessage.created_at >= since_dt,
        )
        .order_by(DMMessage.created_at.asc())
        .first()
    )


def create_message_and_delivery(
    db: Session,
    *,
    message_id,
    conversation_id,
    sender_user_id: int,
    sender_device_id,
    ciphertext: bytes,
    proto: int,
    client_message_id,
    recipient_user_id: int,
):
    from models import DMDelivery, DMMessage

    new_message = DMMessage(
        id=message_id,
        conversation_id=conversation_id,
        sender_user_id=sender_user_id,
        sender_device_id=sender_device_id,
        ciphertext=ciphertext,
        proto=proto,
        client_message_id=client_message_id,
    )
    db.add(new_message)

    delivery_record = DMDelivery(
        message_id=new_message.id, recipient_user_id=recipient_user_id
    )
    db.add(delivery_record)
    return new_message, delivery_record


def list_messages_for_conversation(db: Session, *, conversation_id, limit: int):
    from sqlalchemy import desc

    from models import DMMessage

    return (
        db.query(DMMessage)
        .filter(DMMessage.conversation_id == conversation_id)
        .order_by(desc(DMMessage.created_at), desc(DMMessage.id))
        .limit(limit)
        .all()
    )


def list_messages_for_conversation_before(
    db: Session, *, conversation_id, limit: int, cursor_created_at, cursor_id
):
    from sqlalchemy import and_, desc, or_

    from models import DMMessage

    return (
        db.query(DMMessage)
        .filter(
            DMMessage.conversation_id == conversation_id,
            or_(
                DMMessage.created_at < cursor_created_at,
                and_(
                    DMMessage.created_at == cursor_created_at, DMMessage.id < cursor_id
                ),
            ),
        )
        .order_by(desc(DMMessage.created_at), desc(DMMessage.id))
        .limit(limit)
        .all()
    )


def get_message(db: Session, *, message_id):
    from models import DMMessage

    return db.query(DMMessage).filter(DMMessage.id == message_id).first()


def get_delivery(db: Session, *, message_id, recipient_user_id: int):
    from models import DMDelivery

    return (
        db.query(DMDelivery)
        .filter(
            DMDelivery.message_id == message_id,
            DMDelivery.recipient_user_id == recipient_user_id,
        )
        .first()
    )


# --- Status ---


def lock_user(db: Session, *, user_id: int):
    from core.users import get_user_by_id_for_update

    return get_user_by_id_for_update(db, account_id=user_id)


def count_status_posts_since(db: Session, *, owner_user_id: int, since_dt) -> int:
    from models import StatusPost

    return (
        db.query(StatusPost)
        .filter(StatusPost.owner_user_id == owner_user_id, StatusPost.created_at >= since_dt)
        .count()
    )


def list_user_contacts(db: Session, *, user_id: int):
    from models import DMParticipant

    conversation_ids = (
        db.query(DMParticipant.conversation_id)
        .filter(DMParticipant.user_id == user_id)
        .subquery()
    )
    contacts = (
        db.query(DMParticipant.user_id)
        .filter(DMParticipant.conversation_id.in_(conversation_ids), DMParticipant.user_id != user_id)
        .distinct()
        .all()
    )
    return [row[0] for row in contacts]


def create_status_post(
    db: Session,
    *,
    post_id,
    owner_user_id: int,
    media_meta: dict,
    audience_mode: str,
    expires_at,
    post_epoch: int = 0,
):
    from models import StatusPost

    new_post = StatusPost(
        id=post_id,
        owner_user_id=owner_user_id,
        media_meta=media_meta,
        audience_mode=audience_mode,
        expires_at=expires_at,
        post_epoch=post_epoch,
    )
    db.add(new_post)
    return new_post


def create_status_audience_rows(db: Session, *, post_id, viewer_user_ids):
    from models import StatusAudience

    rows = [
        StatusAudience(post_id=post_id, viewer_user_id=viewer_id)
        for viewer_id in viewer_user_ids
    ]
    if rows:
        db.bulk_save_objects(rows)
    return rows


def list_status_feed_posts(db: Session, *, viewer_user_id: int, now_dt, limit: int):
    from sqlalchemy import desc

    from models import StatusAudience, StatusPost

    query = (
        db.query(StatusPost)
        .join(StatusAudience, StatusPost.id == StatusAudience.post_id)
        .filter(
            StatusAudience.viewer_user_id == viewer_user_id,
            StatusPost.expires_at > now_dt,
        )
    )
    return query.order_by(desc(StatusPost.created_at), desc(StatusPost.id)).limit(limit).all()


def get_status_post(db: Session, *, post_id):
    from models import StatusPost

    return db.query(StatusPost).filter(StatusPost.id == post_id).first()


def list_status_feed_posts_before(
    db: Session, *, viewer_user_id: int, now_dt, limit: int, cursor_created_at, cursor_id
):
    from sqlalchemy import desc

    from models import StatusAudience, StatusPost

    query = (
        db.query(StatusPost)
        .join(StatusAudience, StatusPost.id == StatusAudience.post_id)
        .filter(
            StatusAudience.viewer_user_id == viewer_user_id,
            StatusPost.expires_at > now_dt,
            (StatusPost.created_at < cursor_created_at)
            | ((StatusPost.created_at == cursor_created_at) & (StatusPost.id < cursor_id)),
        )
    )
    return query.order_by(desc(StatusPost.created_at), desc(StatusPost.id)).limit(limit).all()


def list_allowed_audience_post_ids(db: Session, *, viewer_user_id: int, post_ids):
    from models import StatusAudience

    rows = (
        db.query(StatusAudience.post_id)
        .filter(
            StatusAudience.post_id.in_(post_ids),
            StatusAudience.viewer_user_id == viewer_user_id,
        )
        .all()
    )
    return {row[0] for row in rows}


def list_existing_status_views(db: Session, *, viewer_user_id: int, post_ids):
    from models import StatusView

    rows = (
        db.query(StatusView.post_id)
        .filter(
            StatusView.post_id.in_(post_ids),
            StatusView.viewer_user_id == viewer_user_id,
        )
        .all()
    )
    return {row[0] for row in rows}


def insert_status_views(db: Session, *, rows):
    from models import StatusView

    dialect_name = db.bind.dialect.name if db.bind else ""
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(StatusView).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["post_id", "viewer_user_id"])
        db.execute(stmt)
        return
    if dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        stmt = sqlite_insert(StatusView).values(rows).prefix_with("OR IGNORE")
        db.execute(stmt)
        return

    db.bulk_save_objects(
        [
            StatusView(
                post_id=row["post_id"],
                viewer_user_id=row["viewer_user_id"],
                viewed_at=row["viewed_at"],
            )
            for row in rows
        ]
    )


def delete_status_post_cascade(db: Session, *, post_id):
    from models import StatusAudience, StatusView

    db.query(StatusAudience).filter(StatusAudience.post_id == post_id).delete()
    db.query(StatusView).filter(StatusView.post_id == post_id).delete()


def list_user_presences(db: Session, *, user_ids):
    from models import UserPresence

    if not user_ids:
        return []
    return db.query(UserPresence).filter(UserPresence.user_id.in_(user_ids)).all()


def list_blocks_involving_user(db: Session, *, current_user_id: int, user_ids):
    from sqlalchemy import or_

    from models import Block

    if not user_ids:
        return []
    return (
        db.query(Block.blocker_id, Block.blocked_id)
        .filter(
            or_(Block.blocker_id == current_user_id, Block.blocked_id == current_user_id),
            or_(Block.blocker_id.in_(user_ids), Block.blocked_id.in_(user_ids)),
        )
        .all()
    )


# --- Global chat ---


def list_global_chat_messages(db: Session, *, limit: int, before):
    from sqlalchemy.orm import joinedload

    from models import GlobalChatMessage

    query = (
        db.query(GlobalChatMessage)
        .options(joinedload(GlobalChatMessage.user))
        .order_by(GlobalChatMessage.created_at.desc())
    )

    if before:
        before_msg = db.query(GlobalChatMessage).filter(GlobalChatMessage.id == before).first()
        if before_msg:
            query = query.filter(GlobalChatMessage.created_at < before_msg.created_at)

    return query.limit(limit).all()


def list_global_chat_messages_by_ids(db: Session, *, ids):
    from sqlalchemy.orm import joinedload

    from models import GlobalChatMessage

    if not ids:
        return []
    return (
        db.query(GlobalChatMessage)
        .options(joinedload(GlobalChatMessage.user))
        .filter(GlobalChatMessage.id.in_(list(ids)))
        .all()
    )


def upsert_global_chat_viewer_last_seen(db: Session, *, user_id: int, now_dt):
    from sqlalchemy import exc

    from models import GlobalChatViewer

    try:
        existing_viewer = (
            db.query(GlobalChatViewer).filter(GlobalChatViewer.user_id == user_id).first()
        )
        if existing_viewer:
            existing_viewer.last_seen = now_dt
        else:
            db.add(GlobalChatViewer(user_id=user_id, last_seen=now_dt))
            db.flush()
    except exc.IntegrityError:
        db.rollback()
        existing_viewer = (
            db.query(GlobalChatViewer).filter(GlobalChatViewer.user_id == user_id).first()
        )
        if existing_viewer:
            existing_viewer.last_seen = now_dt


def count_global_chat_viewers_since(db: Session, *, cutoff_dt) -> int:
    from models import GlobalChatViewer

    return db.query(GlobalChatViewer).filter(GlobalChatViewer.last_seen >= cutoff_dt).count()


def delete_global_chat_messages_before(db: Session, *, cutoff_dt) -> int:
    from models import GlobalChatMessage

    return db.query(GlobalChatMessage).filter(GlobalChatMessage.created_at < cutoff_dt).delete()


def get_global_chat_message_by_client_id(db: Session, *, user_id: int, client_message_id: str):
    from models import GlobalChatMessage

    return (
        db.query(GlobalChatMessage)
        .filter(
            GlobalChatMessage.user_id == user_id,
            GlobalChatMessage.client_message_id == client_message_id,
        )
        .first()
    )


def list_recent_global_chat_message_ids_since(db: Session, *, user_id: int, since_dt, limit: int):
    from models import GlobalChatMessage

    return (
        db.query(GlobalChatMessage.id)
        .filter(GlobalChatMessage.user_id == user_id, GlobalChatMessage.created_at >= since_dt)
        .order_by(GlobalChatMessage.created_at.desc())
        .limit(limit)
        .all()
    )


def get_global_chat_message_with_user(db: Session, *, message_id: int):
    from sqlalchemy.orm import joinedload

    from models import GlobalChatMessage

    return (
        db.query(GlobalChatMessage)
        .options(joinedload(GlobalChatMessage.user))
        .filter(GlobalChatMessage.id == message_id)
        .first()
    )


def create_global_chat_message(
    db: Session,
    *,
    user_id: int,
    message: str,
    client_message_id,
    reply_to_message_id,
):
    from models import GlobalChatMessage

    new_message = GlobalChatMessage(
        user_id=user_id,
        message=message,
        client_message_id=client_message_id,
        reply_to_message_id=reply_to_message_id,
    )
    db.add(new_message)
    return new_message


# --- Private chat conversations/messages ---


def get_admin_user_id(db: Session):
    from models import AdminUser

    admin_entry = db.query(AdminUser).first()
    return admin_entry.user_id if admin_entry else None


def get_private_chat_conversation_by_users(db: Session, *, user1_id: int, user2_id: int):
    from models import PrivateChatConversation

    return (
        db.query(PrivateChatConversation)
        .filter(
            PrivateChatConversation.user1_id == user1_id,
            PrivateChatConversation.user2_id == user2_id,
        )
        .first()
    )


def get_private_chat_conversation(db: Session, *, conversation_id: int):
    from models import PrivateChatConversation

    return (
        db.query(PrivateChatConversation)
        .filter(PrivateChatConversation.id == conversation_id)
        .first()
    )


def create_private_chat_conversation(
    db: Session,
    *,
    user1_id: int,
    user2_id: int,
    requested_by: int,
    status: str,
    responded_at,
):
    from models import PrivateChatConversation

    conversation = PrivateChatConversation(
        user1_id=user1_id,
        user2_id=user2_id,
        requested_by=requested_by,
        status=status,
        responded_at=responded_at,
    )
    db.add(conversation)
    return conversation


def count_private_chat_messages(db: Session, *, conversation_id: int) -> int:
    from sqlalchemy import func

    from models import PrivateChatMessage

    return (
        db.query(func.count(PrivateChatMessage.id))
        .filter(PrivateChatMessage.conversation_id == conversation_id)
        .scalar()
        or 0
    )


def get_private_chat_message_by_client_id(
    db: Session,
    *,
    conversation_id: int,
    sender_id: int,
    client_message_id: str,
):
    from models import PrivateChatMessage

    return (
        db.query(PrivateChatMessage)
        .filter(
            PrivateChatMessage.conversation_id == conversation_id,
            PrivateChatMessage.sender_id == sender_id,
            PrivateChatMessage.client_message_id == client_message_id,
        )
        .first()
    )


def count_private_chat_messages_since(db: Session, *, sender_id: int, since_dt) -> int:
    from sqlalchemy import func

    from models import PrivateChatMessage

    return (
        db.query(func.count(PrivateChatMessage.id))
        .filter(
            PrivateChatMessage.sender_id == sender_id,
            PrivateChatMessage.created_at >= since_dt,
        )
        .scalar()
        or 0
    )


def get_private_chat_message_in_conversation(
    db: Session,
    *,
    message_id: int,
    conversation_id: int,
):
    from models import PrivateChatMessage

    return (
        db.query(PrivateChatMessage)
        .filter(
            PrivateChatMessage.id == message_id,
            PrivateChatMessage.conversation_id == conversation_id,
        )
        .first()
    )


def create_private_chat_message(
    db: Session,
    *,
    conversation_id: int,
    sender_id: int,
    message: str,
    status: str,
    client_message_id,
    reply_to_message_id,
):
    from models import PrivateChatMessage

    new_message = PrivateChatMessage(
        conversation_id=conversation_id,
        sender_id=sender_id,
        message=message,
        status=status,
        client_message_id=client_message_id,
        reply_to_message_id=reply_to_message_id,
    )
    db.add(new_message)
    return new_message


def list_private_chat_conversations_for_user(db: Session, *, user_id: int):
    from sqlalchemy import func, or_

    from models import PrivateChatConversation

    return (
        db.query(PrivateChatConversation)
        .filter(
            or_(
                PrivateChatConversation.user1_id == user_id,
                PrivateChatConversation.user2_id == user_id,
            ),
            or_(
                PrivateChatConversation.status == "accepted",
                PrivateChatConversation.status == "pending",
            ),
        )
        .order_by(
            func.coalesce(
                PrivateChatConversation.last_message_at,
                PrivateChatConversation.created_at,
            ).desc()
        )
        .all()
    )


def list_unread_counts_for_user_as_user1(db: Session, *, conversation_ids, user_id: int):
    from sqlalchemy import func, or_

    from models import PrivateChatConversation, PrivateChatMessage

    return (
        db.query(PrivateChatMessage.conversation_id, func.count(PrivateChatMessage.id))
        .join(
            PrivateChatConversation,
            PrivateChatConversation.id == PrivateChatMessage.conversation_id,
        )
        .filter(
            PrivateChatMessage.conversation_id.in_(conversation_ids),
            PrivateChatMessage.sender_id != user_id,
            or_(
                PrivateChatConversation.last_read_message_id_user1.is_(None),
                PrivateChatMessage.id > PrivateChatConversation.last_read_message_id_user1,
            ),
        )
        .group_by(PrivateChatMessage.conversation_id)
        .all()
    )


def list_unread_counts_for_user_as_user2(db: Session, *, conversation_ids, user_id: int):
    from sqlalchemy import func, or_

    from models import PrivateChatConversation, PrivateChatMessage

    return (
        db.query(PrivateChatMessage.conversation_id, func.count(PrivateChatMessage.id))
        .join(
            PrivateChatConversation,
            PrivateChatConversation.id == PrivateChatMessage.conversation_id,
        )
        .filter(
            PrivateChatMessage.conversation_id.in_(conversation_ids),
            PrivateChatMessage.sender_id != user_id,
            or_(
                PrivateChatConversation.last_read_message_id_user2.is_(None),
                PrivateChatMessage.id > PrivateChatConversation.last_read_message_id_user2,
            ),
        )
        .group_by(PrivateChatMessage.conversation_id)
        .all()
    )


def list_user_presence_rows(db: Session, *, user_ids):
    from models import UserPresence

    if not user_ids:
        return []
    return db.query(UserPresence).filter(UserPresence.user_id.in_(list(user_ids))).all()


def get_user_presence(db: Session, *, user_id: int):
    from models import UserPresence

    return db.query(UserPresence).filter(UserPresence.user_id == user_id).first()


def create_user_presence(db: Session, *, user_id: int, last_seen_at, device_online: bool, privacy_settings):
    from models import UserPresence

    presence = UserPresence(
        user_id=user_id,
        last_seen_at=last_seen_at,
        device_online=device_online,
        privacy_settings=privacy_settings,
    )
    db.add(presence)
    return presence


def list_private_chat_last_messages(db: Session, *, conversation_ids, peer_ids):
    from sqlalchemy import func

    from models import PrivateChatMessage

    return (
        db.query(
            PrivateChatMessage.conversation_id,
            PrivateChatMessage.sender_id,
            func.max(PrivateChatMessage.created_at),
        )
        .filter(
            PrivateChatMessage.conversation_id.in_(conversation_ids),
            PrivateChatMessage.sender_id.in_(list(peer_ids)),
        )
        .group_by(PrivateChatMessage.conversation_id, PrivateChatMessage.sender_id)
        .all()
    )


def list_private_chat_messages_with_sender(
    db: Session, *, conversation_id: int, limit: int
):
    from sqlalchemy.orm import joinedload

    from models import PrivateChatMessage

    return (
        db.query(PrivateChatMessage)
        .options(joinedload(PrivateChatMessage.sender))
        .filter(PrivateChatMessage.conversation_id == conversation_id)
        .order_by(PrivateChatMessage.created_at.desc())
        .limit(limit)
        .all()
    )


def list_private_chat_messages_with_sender_by_ids(
    db: Session, *, conversation_id: int, message_ids
):
    from sqlalchemy.orm import joinedload

    from models import PrivateChatMessage

    if not message_ids:
        return []
    return (
        db.query(PrivateChatMessage)
        .options(joinedload(PrivateChatMessage.sender))
        .filter(
            PrivateChatMessage.id.in_(list(message_ids)),
            PrivateChatMessage.conversation_id == conversation_id,
        )
        .all()
    )


def get_latest_private_chat_message(db: Session, *, conversation_id: int):
    from models import PrivateChatMessage

    return (
        db.query(PrivateChatMessage)
        .filter(PrivateChatMessage.conversation_id == conversation_id)
        .order_by(PrivateChatMessage.id.desc())
        .first()
    )


def get_latest_private_chat_message_for_sender_in_conversation(
    db: Session, *, conversation_id: int, sender_id: int
):
    from models import PrivateChatMessage

    return (
        db.query(PrivateChatMessage)
        .filter(
            PrivateChatMessage.conversation_id == conversation_id,
            PrivateChatMessage.sender_id == sender_id,
        )
        .order_by(PrivateChatMessage.created_at.desc())
        .first()
    )


def get_latest_private_chat_message_for_sender(db: Session, *, sender_id: int):
    from models import PrivateChatMessage

    return (
        db.query(PrivateChatMessage)
        .filter(PrivateChatMessage.sender_id == sender_id)
        .order_by(PrivateChatMessage.created_at.desc())
        .first()
    )


def get_private_chat_message(db: Session, *, message_id: int):
    from models import PrivateChatMessage

    return db.query(PrivateChatMessage).filter(PrivateChatMessage.id == message_id).first()


# --- Private chat blocks ---


def get_user_by_account_id(db: Session, *, user_id: int):
    from core.users import get_user_by_id

    return get_user_by_id(db, account_id=user_id)


def get_private_chat_block(db: Session, *, blocker_id: int, blocked_id: int):
    from models import Block

    return (
        db.query(Block)
        .filter(Block.blocker_id == blocker_id, Block.blocked_id == blocked_id)
        .first()
    )


def create_private_chat_block(db: Session, *, blocker_id: int, blocked_id: int, created_at):
    from models import Block

    new_block = Block(blocker_id=blocker_id, blocked_id=blocked_id, created_at=created_at)
    db.add(new_block)
    return new_block


def list_pending_private_chat_conversations_between(db: Session, *, user_a: int, user_b: int):
    from models import PrivateChatConversation

    user_ids = sorted([user_a, user_b])
    return (
        db.query(PrivateChatConversation)
        .filter(
            PrivateChatConversation.user1_id == user_ids[0],
            PrivateChatConversation.user2_id == user_ids[1],
            PrivateChatConversation.status == "pending",
        )
        .all()
    )


def list_blocks_for_user(db: Session, *, blocker_id: int):
    from sqlalchemy import desc

    from models import Block

    return (
        db.query(Block)
        .filter(Block.blocker_id == blocker_id)
        .order_by(desc(Block.created_at))
        .all()
    )


def list_users_by_account_ids(db: Session, *, user_ids):
    if not user_ids:
        return []
    from core.users import get_users_by_ids

    return get_users_by_ids(db, account_ids=list(user_ids))


# --- Generic blocks (DM/privacy) ---


def get_block(db: Session, *, blocker_id: int, blocked_id: int):
    from models import Block

    return (
        db.query(Block)
        .filter(Block.blocker_id == blocker_id, Block.blocked_id == blocked_id)
        .first()
    )


def create_block(db: Session, *, blocker_id: int, blocked_id: int, created_at):
    from models import Block

    block = Block(blocker_id=blocker_id, blocked_id=blocked_id, created_at=created_at)
    db.add(block)
    return block


def list_blocks(db: Session, *, blocker_id: int, limit: int, offset: int):
    from sqlalchemy import desc

    from models import Block

    return (
        db.query(Block)
        .filter(Block.blocker_id == blocker_id)
        .order_by(desc(Block.created_at))
        .offset(offset)
        .limit(limit)
        .all()
    )


# --- DM conversations ---


def get_dm_conversation_by_pair_key(db: Session, *, pair_key: str):
    from models import DMConversation

    if not hasattr(DMConversation, "pair_key"):
        return None
    return db.query(DMConversation).filter(DMConversation.pair_key == pair_key).first()


def find_dm_conversation_between_users(db: Session, *, user_ids):
    from sqlalchemy import func

    from models import DMConversation, DMParticipant

    return (
        db.query(DMConversation)
        .join(DMParticipant, DMConversation.id == DMParticipant.conversation_id)
        .filter(DMParticipant.user_id.in_(list(user_ids)))
        .group_by(DMConversation.id)
        .having(func.count(func.distinct(DMParticipant.user_id)) == 2)
        .first()
    )


def list_active_e2ee_device_ids_for_users(db: Session, *, user_ids):
    from models import E2EEDevice

    rows = (
        db.query(E2EEDevice.user_id, E2EEDevice.device_id)
        .filter(E2EEDevice.user_id.in_(list(user_ids)), E2EEDevice.status == "active")
        .all()
    )
    device_map = {user_id: [] for user_id in user_ids}
    for user_id, device_id in rows:
        device_map[user_id].append(str(device_id))
    return device_map


def list_dm_participants(db: Session, *, conversation_id):
    from models import DMParticipant

    return (
        db.query(DMParticipant)
        .filter(DMParticipant.conversation_id == conversation_id)
        .all()
    )


def get_dm_participant(db: Session, *, conversation_id, user_id: int):
    from models import DMParticipant

    return (
        db.query(DMParticipant)
        .filter(DMParticipant.conversation_id == conversation_id, DMParticipant.user_id == user_id)
        .first()
    )


def create_dm_conversation(db: Session, *, conversation_id, created_at, pair_key=None):
    from models import DMConversation

    if pair_key and hasattr(DMConversation, "pair_key"):
        conv = DMConversation(id=conversation_id, created_at=created_at, pair_key=pair_key)
    else:
        conv = DMConversation(id=conversation_id, created_at=created_at)
    db.add(conv)
    return conv


def create_dm_participant(db: Session, *, conversation_id, user_id: int, device_ids):
    from models import DMParticipant

    participant = DMParticipant(conversation_id=conversation_id, user_id=user_id, device_ids=device_ids)
    db.add(participant)
    return participant


def list_user_dm_conversations(db: Session, *, user_id: int, limit: int, offset: int):
    from sqlalchemy import func

    from models import DMConversation, DMParticipant

    return (
        db.query(DMConversation)
        .join(DMParticipant, DMConversation.id == DMParticipant.conversation_id)
        .filter(DMParticipant.user_id == user_id)
        .order_by(func.coalesce(DMConversation.last_message_at, DMConversation.created_at).desc(), DMConversation.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


# --- Status metrics ---


def get_status_post_counts(db: Session, *, today_start, now_dt):
    from sqlalchemy import case, func

    from models import StatusPost

    return db.query(
        func.sum(case((StatusPost.created_at >= today_start, 1), else_=0)),
        func.sum(case((StatusPost.expires_at > now_dt, 1), else_=0)),
        func.sum(case((StatusPost.expires_at <= now_dt, 1), else_=0)),
    ).first()


def count_status_views_since(db: Session, *, since_dt):
    from sqlalchemy import func

    from models import StatusView

    return (
        db.query(func.count(StatusView.post_id))
        .filter(StatusView.viewed_at >= since_dt)
        .scalar()
        or 0
    )


def get_avg_status_audience_size(db: Session):
    from sqlalchemy import func

    from models import StatusAudience

    audience_counts = (
        db.query(
            StatusAudience.post_id.label("post_id"),
            func.count(StatusAudience.viewer_user_id).label("viewer_count"),
        )
        .group_by(StatusAudience.post_id)
        .subquery()
    )
    return db.query(func.avg(audience_counts.c.viewer_count)).scalar() or 0


# --- Group metrics ---


def get_group_counts(db: Session):
    from sqlalchemy import func

    from models import Group

    return db.query(
        func.count(Group.id).label("total"),
        func.count(Group.id).filter(Group.is_closed.is_(False)).label("active"),
    ).one()


def get_avg_group_size(db: Session):
    from sqlalchemy import func

    from models import GroupParticipant

    participant_counts_subq = (
        db.query(
            GroupParticipant.group_id.label("group_id"),
            func.count(GroupParticipant.user_id).label("participant_count"),
        )
        .filter(GroupParticipant.is_banned.is_(False))
        .group_by(GroupParticipant.group_id)
        .subquery()
    )
    return db.query(func.avg(participant_counts_subq.c.participant_count)).scalar() or 0


def get_group_message_counts(db: Session, *, today_start, last_hour_start):
    from sqlalchemy import func

    from models import GroupMessage

    return db.query(
        func.count(GroupMessage.id)
        .filter(GroupMessage.created_at >= today_start)
        .label("today"),
        func.count(GroupMessage.id)
        .filter(GroupMessage.created_at >= last_hour_start)
        .label("last_hour"),
    ).one()


def count_group_sender_keys(db: Session):
    from models import GroupSenderKey

    return db.query(GroupSenderKey).count()


def count_groups_with_recent_epoch_changes(db: Session, *, since_dt):
    from models import Group

    return db.query(Group).filter(Group.updated_at >= since_dt).count()


# --- E2EE keys ---


def get_e2ee_device(db: Session, *, device_id):
    from models import E2EEDevice

    return db.query(E2EEDevice).filter(E2EEDevice.device_id == device_id).first()


def get_user_e2ee_device(db: Session, *, user_id: int, device_id):
    from models import E2EEDevice

    return (
        db.query(E2EEDevice)
        .filter(E2EEDevice.device_id == device_id, E2EEDevice.user_id == user_id)
        .first()
    )


def create_e2ee_device(db: Session, *, device_id, user_id: int, device_name: str):
    from models import E2EEDevice

    device = E2EEDevice(
        device_id=device_id,
        user_id=user_id,
        device_name=device_name,
        status="active",
    )
    db.add(device)
    return device


def get_e2ee_key_bundle(db: Session, *, device_id):
    from models import E2EEKeyBundle

    return (
        db.query(E2EEKeyBundle)
        .filter(E2EEKeyBundle.device_id == device_id)
        .first()
    )


def count_identity_change_revocations(db: Session, *, device_id):
    from models import DeviceRevocation

    return (
        db.query(DeviceRevocation)
        .filter(
            DeviceRevocation.device_id == device_id,
            DeviceRevocation.reason.in_(["identity_change", "identity_change_block"]),
        )
        .count()
    )


def create_device_revocation(db: Session, *, user_id: int, device_id, reason: str):
    from models import DeviceRevocation

    revocation = DeviceRevocation(
        user_id=user_id,
        device_id=device_id,
        reason=reason,
    )
    db.add(revocation)
    return revocation


def delete_unclaimed_prekeys(db: Session, *, device_id):
    from models import E2EEOneTimePrekey

    return (
        db.query(E2EEOneTimePrekey)
        .filter(
            E2EEOneTimePrekey.device_id == device_id,
            E2EEOneTimePrekey.claimed == False,
        )
        .delete(synchronize_session=False)
    )


def bulk_insert_prekeys(db: Session, *, device_id, prekeys):
    from models import E2EEOneTimePrekey

    objects = [
        E2EEOneTimePrekey(device_id=device_id, prekey_pub=prekey_pub, claimed=False)
        for prekey_pub in prekeys
    ]
    db.bulk_save_objects(objects)
    return objects


def count_unclaimed_prekeys(db: Session, *, device_id):
    from models import E2EEOneTimePrekey

    return (
        db.query(E2EEOneTimePrekey)
        .filter(
            E2EEOneTimePrekey.device_id == device_id,
            E2EEOneTimePrekey.claimed == False,
        )
        .count()
    )


def list_active_devices_with_bundles(db: Session, *, user_id: int):
    from sqlalchemy import func

    from models import E2EEDevice, E2EEKeyBundle, E2EEOneTimePrekey

    return (
        db.query(
            E2EEDevice.device_id,
            E2EEDevice.device_name,
            E2EEKeyBundle.identity_key_pub,
            E2EEKeyBundle.signed_prekey_pub,
            E2EEKeyBundle.signed_prekey_sig,
            E2EEKeyBundle.bundle_version,
            func.count(E2EEOneTimePrekey.id)
            .filter(E2EEOneTimePrekey.claimed == False)
            .label("available"),
        )
        .join(E2EEKeyBundle, E2EEKeyBundle.device_id == E2EEDevice.device_id)
        .outerjoin(
            E2EEOneTimePrekey, E2EEOneTimePrekey.device_id == E2EEDevice.device_id
        )
        .filter(E2EEDevice.user_id == user_id, E2EEDevice.status == "active")
        .group_by(
            E2EEDevice.device_id,
            E2EEDevice.device_name,
            E2EEKeyBundle.identity_key_pub,
            E2EEKeyBundle.signed_prekey_pub,
            E2EEKeyBundle.signed_prekey_sig,
            E2EEKeyBundle.bundle_version,
        )
        .all()
    )


def list_user_devices(db: Session, *, user_id: int):
    from sqlalchemy import desc

    from models import E2EEDevice

    return (
        db.query(E2EEDevice)
        .filter(E2EEDevice.user_id == user_id)
        .order_by(desc(E2EEDevice.created_at))
        .all()
    )


def claim_prekey(db: Session, *, device_id, prekey_id):
    from sqlalchemy import update

    from models import E2EEOneTimePrekey

    result = db.execute(
        update(E2EEOneTimePrekey)
        .where(
            E2EEOneTimePrekey.id == prekey_id,
            E2EEOneTimePrekey.device_id == device_id,
            E2EEOneTimePrekey.claimed == False,
        )
        .values(claimed=True)
        .returning(E2EEOneTimePrekey.id)
    )
    return result.scalar_one_or_none()


def list_dm_participants_for_conversations(db: Session, *, conversation_ids, exclude_user_id: int):
    from models import DMParticipant

    return (
        db.query(DMParticipant)
        .filter(DMParticipant.conversation_id.in_(list(conversation_ids)), DMParticipant.user_id != exclude_user_id)
        .all()
    )


def count_unread_dm_messages_for_conversations(db: Session, *, conversation_ids, recipient_user_id: int, exclude_sender_id: int):
    from sqlalchemy import and_, func, or_

    from models import DMDelivery, DMMessage

    rows = (
        db.query(DMMessage.conversation_id, func.count(DMMessage.id))
        .outerjoin(
            DMDelivery,
            and_(
                DMDelivery.message_id == DMMessage.id,
                DMDelivery.recipient_user_id == recipient_user_id,
            ),
        )
        .filter(
            DMMessage.conversation_id.in_(list(conversation_ids)),
            DMMessage.sender_user_id != exclude_sender_id,
            or_(DMDelivery.read_at.is_(None), DMDelivery.id.is_(None)),
        )
        .group_by(DMMessage.conversation_id)
        .all()
    )
    return {cid: count for cid, count in rows}


def get_dm_conversation_by_id(db: Session, *, conversation_id):
    from models import DMConversation

    return db.query(DMConversation).filter(DMConversation.id == conversation_id).first()


# --- DM metrics ---


def list_otpk_stats(db: Session):
    from sqlalchemy import func

    from models import E2EEOneTimePrekey

    return (
        db.query(
            E2EEOneTimePrekey.device_id,
            func.count(E2EEOneTimePrekey.id)
            .filter(E2EEOneTimePrekey.claimed == False)
            .label("available"),
            func.count(E2EEOneTimePrekey.id)
            .filter(E2EEOneTimePrekey.claimed == True)
            .label("claimed"),
        )
        .group_by(E2EEOneTimePrekey.device_id)
        .all()
    )


def count_old_key_bundles(db: Session, *, cutoff_dt):
    from models import E2EEKeyBundle

    return (
        db.query(E2EEKeyBundle)
        .filter(E2EEKeyBundle.updated_at < cutoff_dt)
        .count()
    )


def get_dm_message_counts(db: Session, *, today_start, last_hour_start):
    from sqlalchemy import func

    from models import DMMessage

    return db.query(
        func.count(DMMessage.id)
        .filter(DMMessage.created_at >= today_start)
        .label("today"),
        func.count(DMMessage.id)
        .filter(DMMessage.created_at >= last_hour_start)
        .label("last_hour"),
    ).one()


def get_dm_delivery_counts(db: Session):
    from sqlalchemy import func

    from models import DMDelivery

    return db.query(
        func.count(DMDelivery.id)
        .filter(DMDelivery.delivered_at.is_(None))
        .label("undelivered"),
        func.count(DMDelivery.id)
        .filter(DMDelivery.read_at.is_(None), DMDelivery.delivered_at.isnot(None))
        .label("unread"),
    ).one()


def get_avg_dm_delivery_ms_since(db: Session, *, since_dt):
    from sqlalchemy import func

    from models import DMDelivery, DMMessage

    return (
        db.query(
            func.avg(
                func.extract("epoch", DMDelivery.delivered_at - DMMessage.created_at)
                * 1000
            ).label("avg_delivery_ms")
        )
        .join(DMMessage, DMDelivery.message_id == DMMessage.id)
        .filter(
            DMDelivery.delivered_at >= since_dt,
            DMDelivery.delivered_at.isnot(None),
        )
        .scalar()
    )


def get_device_counts(db: Session):
    from sqlalchemy import func

    from models import E2EEDevice

    return db.query(
        func.count(E2EEDevice.device_id).label("total"),
        func.count(E2EEDevice.device_id)
        .filter(E2EEDevice.status == "active")
        .label("active"),
        func.count(E2EEDevice.device_id)
        .filter(E2EEDevice.status == "revoked")
        .label("revoked"),
    ).one()
