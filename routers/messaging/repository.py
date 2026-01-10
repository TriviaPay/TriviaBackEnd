"""Messaging/Realtime repository layer."""

from sqlalchemy.orm import Session


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
    from models import User

    return db.query(User).filter(User.account_id == user_id).with_for_update().first()


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


# --- Private chat blocks ---


def get_user_by_account_id(db: Session, *, user_id: int):
    from models import User

    return db.query(User).filter(User.account_id == user_id).first()


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
    from models import User

    if not user_ids:
        return []
    return db.query(User).filter(User.account_id.in_(list(user_ids))).all()


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


def list_blocks_with_users(db: Session, *, blocker_id: int, limit: int, offset: int):
    from sqlalchemy import desc

    from models import Block, User

    return (
        db.query(Block, User)
        .join(User, User.account_id == Block.blocked_id)
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
