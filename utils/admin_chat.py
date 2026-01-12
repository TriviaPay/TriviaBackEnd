import logging
import random
from datetime import datetime

from sqlalchemy.orm import Session

from models import AdminUser, PrivateChatConversation, PrivateChatMessage, User

logger = logging.getLogger(__name__)

_WELCOME_MESSAGES = [
    "Hi! This is the Admin chat. Ask any questions or share your concerns here.",
    "Welcome! You can use this Admin chat to ask questions or report any concerns.",
    "Hello! This is the Admin chat—feel free to ask questions or share concerns.",
]


def ensure_admin_conversation_and_message(db: Session, user: User) -> None:
    admin_entry = db.query(AdminUser).first()
    if not admin_entry:
        logger.warning("Admin user is not configured; skipping admin welcome message.")
        return

    admin_id = admin_entry.user_id
    if admin_id == user.account_id:
        return

    user_ids = sorted([admin_id, user.account_id])
    conversation = (
        db.query(PrivateChatConversation)
        .filter(
            PrivateChatConversation.user1_id == user_ids[0],
            PrivateChatConversation.user2_id == user_ids[1],
        )
        .first()
    )

    if not conversation:
        conversation = PrivateChatConversation(
            user1_id=user_ids[0],
            user2_id=user_ids[1],
            requested_by=admin_id,
            status="accepted",
            responded_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
        )
        db.add(conversation)
        db.flush()
    elif conversation.status == "pending":
        conversation.status = "accepted"
        conversation.responded_at = datetime.utcnow()

    existing_count = (
        db.query(PrivateChatMessage)
        .filter(PrivateChatMessage.conversation_id == conversation.id)
        .count()
    )
    if existing_count > 0:
        return

    message = PrivateChatMessage(
        conversation_id=conversation.id,
        sender_id=admin_id,
        message=random.choice(_WELCOME_MESSAGES),
        status="sent",
        created_at=datetime.utcnow(),
    )
    conversation.last_message_at = datetime.utcnow()
    db.add(message)
