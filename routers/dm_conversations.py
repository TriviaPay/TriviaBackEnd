from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from datetime import datetime
from typing import Optional, List
import uuid
import logging

from db import get_db
from models import User, DMConversation, DMParticipant, DMMessage, Block
from routers.dependencies import get_current_user
from config import E2EE_DM_ENABLED

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dm/conversations", tags=["DM Conversations"])


class CreateConversationRequest(BaseModel):
    peer_user_id: int = Field(..., description="User ID of the peer user")


def check_blocked(db: Session, user1_id: int, user2_id: int) -> bool:
    """Check if user1 is blocked by user2 or vice versa."""
    block = db.query(Block).filter(
        or_(
            and_(Block.blocker_id == user1_id, Block.blocked_id == user2_id),
            and_(Block.blocker_id == user2_id, Block.blocked_id == user1_id)
        )
    ).first()
    return block is not None


@router.post("")
async def create_or_find_conversation(
    request: CreateConversationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Find or create a 2-party conversation.
    Idempotent: returns existing conversation if found.
    Checks blocks before creating.
    """
    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=403, detail="E2EE DM is not enabled")
    
    # Validate peer user exists
    peer_user = db.query(User).filter(User.account_id == request.peer_user_id).first()
    if not peer_user:
        raise HTTPException(status_code=404, detail="Peer user not found")
    
    if request.peer_user_id == current_user.account_id:
        raise HTTPException(status_code=400, detail="Cannot create conversation with yourself")
    
    # Check if blocked
    if check_blocked(db, current_user.account_id, request.peer_user_id):
        raise HTTPException(status_code=403, detail="Cannot create conversation with blocked user")
    
    # Find conversations where both users are participants
    # Use stable pair key approach: sort user IDs to create consistent lookup
    user_ids = sorted([current_user.account_id, request.peer_user_id])
    
    # Find conversations where both users are participants
    from sqlalchemy import func
    existing = db.query(DMConversation).join(
        DMParticipant, DMConversation.id == DMParticipant.conversation_id
    ).filter(
        DMParticipant.user_id.in_(user_ids)
    ).group_by(DMConversation.id).having(
        func.count(func.distinct(DMParticipant.user_id)) == 2
    ).first()
    
    if existing:
        # Get participant device lists
        participants = db.query(DMParticipant).filter(
            DMParticipant.conversation_id == existing.id
        ).all()
        
        return {
            "conversation_id": str(existing.id),
            "created_at": existing.created_at.isoformat(),
            "participants": [
                {
                    "user_id": p.user_id,
                    "device_ids": p.device_ids if p.device_ids else []
                }
                for p in participants
            ]
        }
    
    # Create new conversation
    new_conversation = DMConversation(
        id=uuid.uuid4(),
        created_at=datetime.utcnow()
    )
    db.add(new_conversation)
    db.flush()
    
    # Get device IDs for both users
    from models import E2EEDevice
    
    current_user_devices = db.query(E2EEDevice.device_id).filter(
        E2EEDevice.user_id == current_user.account_id,
        E2EEDevice.status == "active"
    ).all()
    current_device_ids = [str(d[0]) for d in current_user_devices]
    
    peer_user_devices = db.query(E2EEDevice.device_id).filter(
        E2EEDevice.user_id == request.peer_user_id,
        E2EEDevice.status == "active"
    ).all()
    peer_device_ids = [str(d[0]) for d in peer_user_devices]
    
    # Create participants
    participant1 = DMParticipant(
        conversation_id=new_conversation.id,
        user_id=current_user.account_id,
        device_ids=current_device_ids
    )
    participant2 = DMParticipant(
        conversation_id=new_conversation.id,
        user_id=request.peer_user_id,
        device_ids=peer_device_ids
    )
    
    db.add(participant1)
    db.add(participant2)
    db.commit()
    db.refresh(new_conversation)
    
    logger.info(f"Created conversation {new_conversation.id} between users {current_user.account_id} and {request.peer_user_id}")
    
    return {
        "conversation_id": str(new_conversation.id),
        "created_at": new_conversation.created_at.isoformat(),
        "participants": [
            {
                "user_id": current_user.account_id,
                "device_ids": current_device_ids
            },
            {
                "user_id": request.peer_user_id,
                "device_ids": peer_device_ids
            }
        ]
    }


@router.get("")
async def list_conversations(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List user's conversations with last message timestamp and unread count.
    """
    try:
        if not E2EE_DM_ENABLED:
            raise HTTPException(status_code=403, detail="E2EE DM is not enabled")
        
        # Get conversations where user is a participant
        # Use coalesce to handle null last_message_at values
        conversations = db.query(DMConversation).join(
            DMParticipant, DMConversation.id == DMParticipant.conversation_id
        ).filter(
            DMParticipant.user_id == current_user.account_id
        ).order_by(
            func.coalesce(DMConversation.last_message_at, DMConversation.created_at).desc(),
            DMConversation.created_at.desc()
        ).offset(offset).limit(limit).all()
        
        result = []
        for conv in conversations:
            try:
                # Get peer user (the other participant)
                participants = db.query(DMParticipant).filter(
                    DMParticipant.conversation_id == conv.id,
                    DMParticipant.user_id != current_user.account_id
                ).first()
                
                if not participants:
                    continue
                
                peer_user = db.query(User).filter(User.account_id == participants.user_id).first()
                if not peer_user:
                    continue
                
                # Count unread messages (messages not read by current user)
                from models import DMDelivery
                unread_count = db.query(DMMessage).outerjoin(
                    DMDelivery,
                    and_(
                        DMDelivery.message_id == DMMessage.id,
                        DMDelivery.recipient_user_id == current_user.account_id
                    )
                ).filter(
                    DMMessage.conversation_id == conv.id,
                    DMMessage.sender_user_id != current_user.account_id,
                    or_(
                        DMDelivery.read_at.is_(None),
                        DMDelivery.id.is_(None)
                    )
                ).count()
                
                result.append({
                    "conversation_id": str(conv.id),
                    "peer_user_id": participants.user_id,
                    "peer_username": peer_user.username if peer_user.username else None,
                    "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
                    "unread_count": unread_count
                })
            except Exception as e:
                logger.error(f"Error processing conversation {conv.id}: {str(e)}", exc_info=True)
                continue
        
        return {
            "conversations": result
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing conversations: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get conversation details including participant device lists.
    """
    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=403, detail="E2EE DM is not enabled")
    
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")
    
    # Verify user is a participant
    participant = db.query(DMParticipant).filter(
        DMParticipant.conversation_id == conv_uuid,
        DMParticipant.user_id == current_user.account_id
    ).first()
    
    if not participant:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    conversation = db.query(DMConversation).filter(
        DMConversation.id == conv_uuid
    ).first()
    
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    # Get all participants with device lists
    participants = db.query(DMParticipant).filter(
        DMParticipant.conversation_id == conv_uuid
    ).all()
    
    return {
        "conversation_id": str(conversation.id),
        "created_at": conversation.created_at.isoformat(),
        "last_message_at": conversation.last_message_at.isoformat() if conversation.last_message_at else None,
        "sealed_sender_enabled": conversation.sealed_sender_enabled,
        "participants": [
            {
                "user_id": p.user_id,
                "device_ids": p.device_ids if p.device_ids else []
            }
            for p in participants
        ]
    }

