"""
Guest user cleanup utilities.
Hard-deletes inactive guest users and all their FK-dependent rows.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Set, Tuple

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from core.config import GUEST_SESSION_EXPIRY_DAYS
from models import (
    Block,
    PrivateChatConversation,
    PrivateChatMessage,
    User,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 100


def _get_fk_tables_for_user() -> List[Tuple[str, List[str]]]:
    """
    Build the FK dependency map dynamically from SQLAlchemy metadata.
    Returns list of (table_name, [fk_column_names]) for tables that reference users.account_id.
    Ignores self-referential FKs (e.g., reply_to_message_id).
    """
    metadata = User.__table__.metadata
    users_table = User.__table__

    fk_tables: List[Tuple[str, List[str]]] = []

    # reversed(sorted_tables) gives children-first order, which is safe for FK deletes
    for table in reversed(metadata.sorted_tables):
        if table.name == users_table.name:
            continue

        user_fk_columns = []
        for fk in table.foreign_keys:
            # Only care about FKs that reference users.account_id
            if fk.column.table.name == users_table.name and fk.column.name == "account_id":
                # Skip self-referential FKs (same table referencing itself)
                if fk.parent.table.name == fk.column.table.name:
                    continue
                user_fk_columns.append(fk.parent.name)

        if user_fk_columns:
            fk_tables.append((table.name, user_fk_columns))

    return fk_tables


def _delete_guest_dependents(db: Session, account_id: int) -> None:
    """
    Delete all FK-dependent rows for a guest user, in correct order.
    Handles multi-FK tables (Block, PrivateChatConversation) and
    the PrivateChatMessage → conversation_id indirect dependency.
    """
    # Step 1: Delete PrivateChatMessages by sender_id
    db.query(PrivateChatMessage).filter(
        PrivateChatMessage.sender_id == account_id
    ).delete(synchronize_session=False)

    # Step 2: Delete PrivateChatMessages by conversation_id for conversations being deleted
    conv_ids = (
        db.query(PrivateChatConversation.id)
        .filter(
            or_(
                PrivateChatConversation.user1_id == account_id,
                PrivateChatConversation.user2_id == account_id,
                PrivateChatConversation.requested_by == account_id,
            )
        )
        .all()
    )
    if conv_ids:
        conv_id_list = [c.id for c in conv_ids]
        db.query(PrivateChatMessage).filter(
            PrivateChatMessage.conversation_id.in_(conv_id_list)
        ).delete(synchronize_session=False)

    # Step 3: Delete PrivateChatConversations
    db.query(PrivateChatConversation).filter(
        or_(
            PrivateChatConversation.user1_id == account_id,
            PrivateChatConversation.user2_id == account_id,
            PrivateChatConversation.requested_by == account_id,
        )
    ).delete(synchronize_session=False)

    # Step 4: Delete Blocks
    db.query(Block).filter(
        or_(
            Block.blocker_id == account_id,
            Block.blocked_id == account_id,
        )
    ).delete(synchronize_session=False)

    # Step 5: Delete all other single-FK tables dynamically
    handled_tables = {
        "private_chat_messages",
        "private_chat_conversations",
        "blocks",
        "users",
    }

    for table_name, fk_columns in _get_fk_tables_for_user():
        if table_name in handled_tables:
            continue

        # Build OR condition for multi-FK columns (shouldn't happen for remaining tables,
        # but handle defensively)
        if len(fk_columns) == 1:
            condition = f"{fk_columns[0]} = :account_id"
        else:
            condition = " OR ".join(f"{col} = :account_id" for col in fk_columns)

        db.execute(
            text(f"DELETE FROM {table_name} WHERE {condition}"),
            {"account_id": account_id},
        )


def cleanup_inactive_guests(db: Session) -> int:
    """
    Hard-delete guest users inactive for longer than GUEST_SESSION_EXPIRY_DAYS.
    Deletes all FK-dependent rows first, then the User row.
    Processes in batches to avoid long-running transactions.

    Returns:
        Number of guest users deleted.
    """
    cutoff = datetime.utcnow() - timedelta(days=GUEST_SESSION_EXPIRY_DAYS)

    total_deleted = 0

    while True:
        # Find a batch of expired guests
        expired_guests = (
            db.query(User.account_id)
            .filter(
                User.is_guest.is_(True),
                or_(
                    User.last_active_at < cutoff,
                    User.last_active_at.is_(None),
                ),
            )
            .limit(BATCH_SIZE)
            .all()
        )

        if not expired_guests:
            break

        for (account_id,) in expired_guests:
            try:
                _delete_guest_dependents(db, account_id)
                db.query(User).filter(User.account_id == account_id).delete(
                    synchronize_session=False
                )
                db.commit()
                total_deleted += 1
            except Exception:
                logger.error(
                    f"Failed to delete guest account_id={account_id}", exc_info=True
                )
                db.rollback()

    logger.info(f"Guest cleanup complete: {total_deleted} inactive guests deleted")
    return total_deleted
