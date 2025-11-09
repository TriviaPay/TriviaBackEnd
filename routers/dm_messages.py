from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc
from datetime import datetime, timedelta
from typing import Optional
import uuid
import base64
import logging

from db import get_db
from models import User, DMConversation, DMParticipant, DMMessage, DMDelivery, Block, E2EEDevice
from routers.dependencies import get_current_user
from config import (
    E2EE_DM_ENABLED,
    E2EE_DM_MAX_MESSAGE_SIZE,
    E2EE_DM_MAX_MESSAGES_PER_MINUTE,
    E2EE_DM_MAX_MESSAGES_PER_CONVERSATION_BURST,
    E2EE_DM_BURST_WINDOW_SECONDS
)
from utils.redis_pubsub import publish_dm_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dm", tags=["DM Messages"])


class SendMessageRequest(BaseModel):
    client_message_id: Optional[str] = Field(None, description="Client-provided ID for idempotency")
    ciphertext: str = Field(..., description="Base64 encoded ciphertext")
    proto: int = Field(..., description="Protocol type: 1=DR message, 2=PreKey message")
    recipient_device_ids: Optional[list] = Field(None, description="Optional list of recipient device IDs")


def check_blocked(db: Session, user1_id: int, user2_id: int) -> bool:
    """Check if user1 is blocked by user2 or vice versa."""
    block = db.query(Block).filter(
        or_(
            and_(Block.blocker_id == user1_id, Block.blocked_id == user2_id),
            and_(Block.blocker_id == user2_id, Block.blocked_id == user1_id)
        )
    ).first()
    return block is not None


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    request: SendMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Send an encrypted message to a conversation.
    Stores ciphertext and publishes to Redis for real-time delivery.
    """
    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=403, detail="E2EE DM is not enabled")
    
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")
    
    # Verify conversation exists and user is a participant
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
    
    # Get sender's active device (for now, use first active device)
    # In production, client should specify which device is sending
    sender_device = db.query(E2EEDevice).filter(
        E2EEDevice.user_id == current_user.account_id,
        E2EEDevice.status == "active"
    ).first()
    
    if not sender_device:
        raise HTTPException(status_code=400, detail="No active device found. Please register a device first.")
    
    # Check if sender device is revoked (shouldn't happen, but safety check)
    if sender_device.status == "revoked":
        raise HTTPException(
            status_code=409,
            detail="DEVICE_REVOKED",
            headers={"X-Error-Code": "DEVICE_REVOKED"}
        )
    
    # Check for duplicate message (idempotent write)
    if request.client_message_id:
        existing_message = db.query(DMMessage).filter(
            DMMessage.conversation_id == conv_uuid,
            DMMessage.sender_user_id == current_user.account_id,
            DMMessage.client_message_id == request.client_message_id
        ).first()
        
        if existing_message:
            logger.debug(f"Duplicate message detected: {request.client_message_id}")
            return {
                "message_id": str(existing_message.id),
                "created_at": existing_message.created_at.isoformat(),
                "duplicate": True
            }
    
    # Decode and validate ciphertext size
    try:
        ciphertext_bytes = base64.b64decode(request.ciphertext)
        if len(ciphertext_bytes) > E2EE_DM_MAX_MESSAGE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"Message exceeds maximum size of {E2EE_DM_MAX_MESSAGE_SIZE} bytes"
            )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid base64 ciphertext: {str(e)}")
    
    # Rate limiting - per user per minute
    one_minute_ago = datetime.utcnow() - timedelta(minutes=1)
    recent_messages = db.query(DMMessage).filter(
        DMMessage.sender_user_id == current_user.account_id,
        DMMessage.created_at >= one_minute_ago
    ).count()
    
    if recent_messages >= E2EE_DM_MAX_MESSAGES_PER_MINUTE:
        # Calculate retry time (seconds until window resets)
        oldest_message = db.query(DMMessage).filter(
            DMMessage.sender_user_id == current_user.account_id,
            DMMessage.created_at >= one_minute_ago
        ).order_by(DMMessage.created_at.asc()).first()
        
        if oldest_message:
            time_until_reset = (oldest_message.created_at + timedelta(minutes=1) - datetime.utcnow()).total_seconds()
            retry_in_seconds = max(1, int(time_until_reset))
        else:
            retry_in_seconds = 60
        
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Maximum {E2EE_DM_MAX_MESSAGES_PER_MINUTE} messages per minute.",
            headers={
                "X-Retry-After": str(retry_in_seconds),
                "X-RateLimit-Limit": str(E2EE_DM_MAX_MESSAGES_PER_MINUTE),
                "X-RateLimit-Remaining": "0"
            }
        )
    
    # Rate limiting - per conversation burst
    burst_window_start = datetime.utcnow() - timedelta(seconds=E2EE_DM_BURST_WINDOW_SECONDS)
    recent_conversation_messages = db.query(DMMessage).filter(
        DMMessage.sender_user_id == current_user.account_id,
        DMMessage.conversation_id == conv_uuid,
        DMMessage.created_at >= burst_window_start
    ).count()
    
    if recent_conversation_messages >= E2EE_DM_MAX_MESSAGES_PER_CONVERSATION_BURST:
        oldest_burst_message = db.query(DMMessage).filter(
            DMMessage.sender_user_id == current_user.account_id,
            DMMessage.conversation_id == conv_uuid,
            DMMessage.created_at >= burst_window_start
        ).order_by(DMMessage.created_at.asc()).first()
        
        if oldest_burst_message:
            time_until_reset = (oldest_burst_message.created_at + timedelta(seconds=E2EE_DM_BURST_WINDOW_SECONDS) - datetime.utcnow()).total_seconds()
            retry_in_seconds = max(1, int(time_until_reset))
        else:
            retry_in_seconds = E2EE_DM_BURST_WINDOW_SECONDS
        
        raise HTTPException(
            status_code=429,
            detail=f"Burst rate limit exceeded. Maximum {E2EE_DM_MAX_MESSAGES_PER_CONVERSATION_BURST} messages per {E2EE_DM_BURST_WINDOW_SECONDS} seconds per conversation.",
            headers={
                "X-Retry-After": str(retry_in_seconds),
                "X-RateLimit-Limit": str(E2EE_DM_MAX_MESSAGES_PER_CONVERSATION_BURST),
                "X-RateLimit-Remaining": "0"
            }
        )
    
    # Get recipient user (the other participant)
    recipient_participant = db.query(DMParticipant).filter(
        DMParticipant.conversation_id == conv_uuid,
        DMParticipant.user_id != current_user.account_id
    ).first()
    
    if not recipient_participant:
        raise HTTPException(status_code=400, detail="Recipient not found in conversation")
    
    recipient_user_id = recipient_participant.user_id
    
    # Check blocks
    if check_blocked(db, current_user.account_id, recipient_user_id):
        raise HTTPException(
            status_code=403,
            detail="BLOCKED",
            headers={"X-Error-Code": "BLOCKED"}
        )
    
    # Create message
    new_message = DMMessage(
        id=uuid.uuid4(),
        conversation_id=conv_uuid,
        sender_user_id=current_user.account_id,
        sender_device_id=sender_device.device_id,
        ciphertext=ciphertext_bytes,
        proto=request.proto,
        client_message_id=request.client_message_id
    )
    
    db.add(new_message)
    
    # Update conversation last_message_at
    conversation.last_message_at = datetime.utcnow()
    
    # Create delivery record for recipient
    delivery_record = DMDelivery(
        message_id=new_message.id,
        recipient_user_id=recipient_user_id
    )
    db.add(delivery_record)
    
    db.commit()
    db.refresh(new_message)
    
    # Publish to Redis for real-time delivery
    event = {
        "type": "dm",
        "message_id": str(new_message.id),
        "conversation_id": conversation_id,
        "sender_user_id": current_user.account_id,
        "sender_device_id": str(sender_device.device_id),
        "ciphertext": request.ciphertext,  # Keep as base64 for JSON
        "proto": request.proto,
        "created_at": new_message.created_at.isoformat()
    }
    
    await publish_dm_message(conversation_id, recipient_user_id, event)
    
    logger.info(f"Message sent: {new_message.id} in conversation {conversation_id}")
    
    return {
        "message_id": str(new_message.id),
        "created_at": new_message.created_at.isoformat(),
        "duplicate": False
    }


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(
    conversation_id: str,
    limit: int = Query(50, ge=1, le=100),
    since: Optional[str] = Query(None, description="Message ID to fetch messages after"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get messages from a conversation.
    Returns ciphertext envelopes only - client decrypts locally.
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
    
    # Build query
    query = db.query(DMMessage).filter(
        DMMessage.conversation_id == conv_uuid
    )
    
    # If since is provided, fetch messages after that ID
    if since:
        try:
            since_uuid = uuid.UUID(since)
            since_message = db.query(DMMessage).filter(DMMessage.id == since_uuid).first()
            if since_message:
                query = query.filter(
                    or_(
                        DMMessage.created_at > since_message.created_at,
                        and_(
                            DMMessage.created_at == since_message.created_at,
                            DMMessage.id > since_uuid
                        )
                    )
                )
        except ValueError:
            pass  # Invalid UUID, ignore since parameter
    
    # Order by created_at desc, then id desc for consistent pagination
    messages = query.order_by(
        desc(DMMessage.created_at),
        desc(DMMessage.id)
    ).limit(limit).all()
    
    # Reverse to get chronological order (oldest first)
    messages = list(reversed(messages))
    
    return {
        "messages": [
            {
                "id": str(msg.id),
                "sender_user_id": msg.sender_user_id,
                "sender_device_id": str(msg.sender_device_id),
                "ciphertext": base64.b64encode(msg.ciphertext).decode('utf-8'),
                "proto": msg.proto,
                "created_at": msg.created_at.isoformat(),
                "client_message_id": msg.client_message_id
            }
            for msg in messages
        ]
    }


@router.post("/messages/{message_id}/delivered")
async def mark_delivered(
    message_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Mark a message as delivered (metadata only).
    """
    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=403, detail="E2EE DM is not enabled")
    
    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID")
    
    message = db.query(DMMessage).filter(DMMessage.id == msg_uuid).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Verify user is the recipient
    delivery = db.query(DMDelivery).filter(
        DMDelivery.message_id == msg_uuid,
        DMDelivery.recipient_user_id == current_user.account_id
    ).first()
    
    if not delivery:
        raise HTTPException(status_code=403, detail="Not authorized to mark this message as delivered")
    
    # Update delivered_at if not already set
    if not delivery.delivered_at:
        delivery.delivered_at = datetime.utcnow()
        db.commit()
    
    return {
        "message_id": message_id,
        "delivered_at": delivery.delivered_at.isoformat()
    }


@router.post("/messages/{message_id}/read")
async def mark_read(
    message_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Mark a message as read (metadata only).
    """
    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=403, detail="E2EE DM is not enabled")
    
    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID")
    
    message = db.query(DMMessage).filter(DMMessage.id == msg_uuid).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Verify user is the recipient
    delivery = db.query(DMDelivery).filter(
        DMDelivery.message_id == msg_uuid,
        DMDelivery.recipient_user_id == current_user.account_id
    ).first()
    
    if not delivery:
        raise HTTPException(status_code=403, detail="Not authorized to mark this message as read")
    
    # Update read_at if not already set
    if not delivery.read_at:
        delivery.read_at = datetime.utcnow()
        db.commit()
    
    return {
        "message_id": message_id,
        "read_at": delivery.read_at.isoformat()
    }

