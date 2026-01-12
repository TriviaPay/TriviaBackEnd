from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

router = APIRouter(prefix="/dm/conversations", tags=["DM Conversations"])

from .schemas import DMCreateConversationRequest
from .service import (
    create_or_find_dm_conversation as service_create_or_find_dm_conversation,
    get_dm_conversation_details as service_get_dm_conversation_details,
    list_dm_conversations as service_list_dm_conversations,
)

@router.post("")
async def create_or_find_conversation(
    request: DMCreateConversationRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Find or create a 2-party conversation.
    Idempotent: returns existing conversation if found.
    Checks blocks before creating.
    """
    return service_create_or_find_dm_conversation(
        db, current_user=current_user, peer_user_id=request.peer_user_id
    )


@router.get("")
async def list_conversations(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    List user's conversations with last message timestamp and unread count.
    """
    return service_list_dm_conversations(
        db, current_user=current_user, limit=limit, offset=offset
    )


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Get conversation details including participant device lists.
    """
    return service_get_dm_conversation_details(
        db, current_user=current_user, conversation_id=conversation_id
    )
