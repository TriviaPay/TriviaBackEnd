from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from models import Block


def check_blocked(db: Session, user1_id: int, user2_id: int) -> bool:
    """Return True when either user blocks the other."""
    block = (
        db.query(Block)
        .filter(
            or_(
                and_(Block.blocker_id == user1_id, Block.blocked_id == user2_id),
                and_(Block.blocker_id == user2_id, Block.blocked_id == user1_id),
            )
        )
        .first()
    )
    return block is not None
