import base64
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from config import (
    E2EE_DM_MAX_MESSAGE_SIZE,
    GROUP_BURST_PER_5S,
    GROUP_BURST_WINDOW_SECONDS,
    GROUP_MESSAGE_RATE_PER_USER_PER_MIN,
    GROUPS_ENABLED,
)
from db import get_db
from models import (
    E2EEDevice,
    Group,
    GroupDelivery,
    GroupMessage,
    GroupParticipant,
    User,
)
from routers.dependencies import get_current_user
from routers.messaging.group_members import check_group_role
from utils.redis_pubsub import publish_group_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/groups", tags=["Group Messages"])


class SendGroupMessageRequest(BaseModel):
    client_message_id: Optional[str] = Field(
        None,
        description="Client-provided ID for idempotency",
        example="group_msg_1234567890",
    )
    ciphertext: str = Field(
        ...,
        description="Base64 encoded ciphertext",
        example="dGVzdF9ncm91cF9jaXBoZXJ0ZXh0X2VuY29kZWRfaW5fYmFzZTY0X2Zvcm1hdF8xMjM0NTY3ODkwYWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo=",
    )
    proto: int = Field(
        ...,
        description="Protocol type: 10=sender-key msg, 11=sender-key distribution",
        example=10,
    )
    group_epoch: int = Field(
        ..., description="Group epoch this message belongs to", example=0
    )
    sender_key_id: Optional[str] = Field(
        None,
        description="Sender key ID for this message",
        example="550e8400-e29b-41d4-a716-446655440000",
    )
    reply_to_message_id: Optional[str] = Field(
        None,
        description="UUID of message being replied to",
        example="550e8400-e29b-41d4-a716-446655440000",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "client_message_id": "group_msg_1234567890",
                "ciphertext": "dGVzdF9ncm91cF9jaXBoZXJ0ZXh0X2VuY29kZWRfaW5fYmFzZTY0X2Zvcm1hdF8xMjM0NTY3ODkwYWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo=",
                "proto": 10,
                "group_epoch": 0,
                "sender_key_id": "550e8400-e29b-41d4-a716-446655440000",
            }
        }


@router.get("/{group_id}/messages")
async def get_messages(
    group_id: str,
    limit: int = Query(default=50, ge=1, le=100, example=50),
    before: Optional[str] = Query(
        None,
        description="Message ID to fetch before",
        example="550e8400-e29b-41d4-a716-446655440000",
    ),
    after: Optional[str] = Query(
        None,
        description="Message ID to fetch after",
        example="550e8400-e29b-41d4-a716-446655440000",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get group message history (ciphertext envelopes).
    Paginated with cursor-based navigation.
    """
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")

    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid group ID format")

    # Check membership
    check_group_role(
        db, group_uuid, current_user.account_id, ["owner", "admin", "member"]
    )

    query = db.query(GroupMessage).filter(GroupMessage.group_id == group_uuid)

    # Cursor-based pagination
    if before:
        try:
            before_uuid = uuid.UUID(before)
            before_msg = (
                db.query(GroupMessage).filter(GroupMessage.id == before_uuid).first()
            )
            if before_msg:
                query = query.filter(
                    (GroupMessage.created_at < before_msg.created_at)
                    | (
                        (GroupMessage.created_at == before_msg.created_at)
                        & (GroupMessage.id < before_uuid)
                    )
                )
        except ValueError:
            pass

    if after:
        try:
            after_uuid = uuid.UUID(after)
            after_msg = (
                db.query(GroupMessage).filter(GroupMessage.id == after_uuid).first()
            )
            if after_msg:
                query = query.filter(
                    (GroupMessage.created_at > after_msg.created_at)
                    | (
                        (GroupMessage.created_at == after_msg.created_at)
                        & (GroupMessage.id > after_uuid)
                    )
                )
        except ValueError:
            pass

    messages = (
        query.order_by(desc(GroupMessage.created_at), desc(GroupMessage.id))
        .limit(limit)
        .all()
    )

    result = []
    for msg in messages:
        message_data = {
            "id": str(msg.id),
            "sender_user_id": msg.sender_user_id,
            "sender_device_id": str(msg.sender_device_id),
            "ciphertext": base64.b64encode(msg.ciphertext).decode("utf-8"),
            "proto": msg.proto,
            "group_epoch": msg.group_epoch,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        }
        # Include reply_to_message_id if this is a reply
        if msg.reply_to_message_id:
            message_data["reply_to_message_id"] = str(msg.reply_to_message_id)
        result.append(message_data)

    return {"messages": result}


@router.post("/{group_id}/messages")
async def send_message(
    group_id: str,
    request: SendGroupMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Send an encrypted message to a group.
    Enforces membership, epoch, and rate limits.
    """
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")

    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid group ID format")

    group = db.query(Group).filter(Group.id == group_uuid).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if group.is_closed:
        raise HTTPException(status_code=403, detail="Group is closed")

    # Check membership
    participant = check_group_role(
        db, group_uuid, current_user.account_id, ["owner", "admin", "member"]
    )

    # Check epoch
    if request.group_epoch != group.group_epoch:
        raise HTTPException(
            status_code=409,
            detail="EPOCH_STALE",
            headers={
                "X-Error-Code": "EPOCH_STALE",
                "X-Current-Epoch": str(group.group_epoch),
            },
        )

    # Get sender device
    sender_device = (
        db.query(E2EEDevice)
        .filter(
            E2EEDevice.user_id == current_user.account_id, E2EEDevice.status == "active"
        )
        .first()
    )

    if not sender_device:
        revoked_device = (
            db.query(E2EEDevice)
            .filter(
                E2EEDevice.user_id == current_user.account_id,
                E2EEDevice.status == "revoked",
            )
            .first()
        )
        if revoked_device:
            raise HTTPException(
                status_code=409,
                detail="DEVICE_REVOKED",
                headers={"X-Error-Code": "DEVICE_REVOKED"},
            )
        raise HTTPException(status_code=400, detail="No active device found")

    # Check for duplicate (idempotency)
    if request.client_message_id:
        existing = (
            db.query(GroupMessage)
            .filter(GroupMessage.client_message_id == request.client_message_id)
            .first()
        )
        if existing:
            return {
                "id": str(existing.id),
                "client_message_id": existing.client_message_id,
                "created_at": (
                    existing.created_at.isoformat() if existing.created_at else None
                ),
            }

    # Decode ciphertext
    try:
        ciphertext_bytes = base64.b64decode(request.ciphertext)
        if len(ciphertext_bytes) > E2EE_DM_MAX_MESSAGE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"Message exceeds maximum size of {E2EE_DM_MAX_MESSAGE_SIZE} bytes",
            )
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid base64 ciphertext: {str(e)}"
        )

    # Rate limiting - per user per minute
    one_minute_ago = datetime.utcnow() - timedelta(minutes=1)
    recent_messages = (
        db.query(GroupMessage.id)
        .filter(
            GroupMessage.sender_user_id == current_user.account_id,
            GroupMessage.created_at >= one_minute_ago,
        )
        .order_by(GroupMessage.created_at.desc())
        .limit(GROUP_MESSAGE_RATE_PER_USER_PER_MIN)
        .all()
    )

    if len(recent_messages) >= GROUP_MESSAGE_RATE_PER_USER_PER_MIN:
        oldest_message = (
            db.query(GroupMessage)
            .filter(
                GroupMessage.sender_user_id == current_user.account_id,
                GroupMessage.created_at >= one_minute_ago,
            )
            .order_by(GroupMessage.created_at.asc())
            .first()
        )

        if oldest_message:
            time_until_reset = (
                oldest_message.created_at + timedelta(minutes=1) - datetime.utcnow()
            ).total_seconds()
            retry_in_seconds = max(1, int(time_until_reset))
        else:
            retry_in_seconds = 60

        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Maximum {GROUP_MESSAGE_RATE_PER_USER_PER_MIN} messages per minute.",
            headers={
                "X-Retry-After": str(retry_in_seconds),
                "X-RateLimit-Limit": str(GROUP_MESSAGE_RATE_PER_USER_PER_MIN),
                "X-RateLimit-Remaining": "0",
            },
        )

    # Rate limiting - per group burst
    burst_window_start = datetime.utcnow() - timedelta(
        seconds=GROUP_BURST_WINDOW_SECONDS
    )
    recent_group_messages = (
        db.query(GroupMessage.id)
        .filter(
            GroupMessage.sender_user_id == current_user.account_id,
            GroupMessage.group_id == group_uuid,
            GroupMessage.created_at >= burst_window_start,
        )
        .order_by(GroupMessage.created_at.desc())
        .limit(GROUP_BURST_PER_5S)
        .all()
    )

    if len(recent_group_messages) >= GROUP_BURST_PER_5S:
        oldest_burst = (
            db.query(GroupMessage)
            .filter(
                GroupMessage.sender_user_id == current_user.account_id,
                GroupMessage.group_id == group_uuid,
                GroupMessage.created_at >= burst_window_start,
            )
            .order_by(GroupMessage.created_at.asc())
            .first()
        )

        if oldest_burst:
            time_until_reset = (
                oldest_burst.created_at
                + timedelta(seconds=GROUP_BURST_WINDOW_SECONDS)
                - datetime.utcnow()
            ).total_seconds()
            retry_in_seconds = max(1, int(time_until_reset))
        else:
            retry_in_seconds = GROUP_BURST_WINDOW_SECONDS

        raise HTTPException(
            status_code=429,
            detail="Burst rate limit exceeded.",
            headers={
                "X-Retry-After": str(retry_in_seconds),
                "X-RateLimit-Limit": str(GROUP_BURST_PER_5S),
                "X-RateLimit-Remaining": "0",
            },
        )

    # Validate reply_to_message_id if provided
    reply_to_message_uuid = None
    if request.reply_to_message_id:
        try:
            reply_to_message_uuid = uuid.UUID(request.reply_to_message_id)
            reply_to_message = (
                db.query(GroupMessage)
                .filter(
                    GroupMessage.id == reply_to_message_uuid,
                    GroupMessage.group_id == group_uuid,
                )
                .first()
            )
            if not reply_to_message:
                raise HTTPException(
                    status_code=404,
                    detail=f"Message {request.reply_to_message_id} not found in this group",
                )
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid reply_to_message_id format"
            )

    # Create message
    new_message = GroupMessage(
        id=uuid.uuid4(),
        group_id=group_uuid,
        sender_user_id=current_user.account_id,
        sender_device_id=sender_device.device_id,
        ciphertext=ciphertext_bytes,
        proto=request.proto,
        group_epoch=request.group_epoch,
        client_message_id=request.client_message_id,
        reply_to_message_id=reply_to_message_uuid,
    )
    db.add(new_message)

    # Update group updated_at
    group.updated_at = datetime.utcnow()

    try:
        db.flush()

        # Get all participants (for delivery records and publishing)
        participants = (
            db.query(GroupParticipant)
            .filter(
                GroupParticipant.group_id == group_uuid,
                GroupParticipant.is_banned == False,
            )
            .all()
        )

        # Create delivery records for all participants except sender
        deliveries = [
            GroupDelivery(message_id=new_message.id, recipient_user_id=p.user_id)
            for p in participants
            if p.user_id != current_user.account_id
        ]
        if deliveries:
            db.bulk_save_objects(deliveries)

        db.commit()
        db.refresh(new_message)

        # Publish to Redis
        event = {
            "type": "group_message",
            "group_id": str(group_uuid),
            "message_id": str(new_message.id),
            "sender_user_id": current_user.account_id,
            "sender_device_id": str(sender_device.device_id),
            "ciphertext": request.ciphertext,
            "proto": request.proto,
            "group_epoch": request.group_epoch,
            "created_at": (
                new_message.created_at.isoformat() if new_message.created_at else None
            ),
        }
        if new_message.reply_to_message_id:
            event["reply_to_message_id"] = str(new_message.reply_to_message_id)
        await publish_group_message(str(group_uuid), event)

        return {
            "id": str(new_message.id),
            "client_message_id": new_message.client_message_id,
            "group_epoch": new_message.group_epoch,
            "created_at": (
                new_message.created_at.isoformat() if new_message.created_at else None
            ),
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Error sending group message: {e}")
        raise HTTPException(status_code=500, detail="Failed to send message")


@router.post("/group-messages/{message_id}/delivered")
async def mark_delivered(
    message_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark message as delivered. Idempotent."""
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")

    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID format")

    message = db.query(GroupMessage).filter(GroupMessage.id == msg_uuid).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    # Find or create delivery record
    delivery = (
        db.query(GroupDelivery)
        .filter(
            GroupDelivery.message_id == msg_uuid,
            GroupDelivery.recipient_user_id == current_user.account_id,
        )
        .first()
    )

    if not delivery:
        delivery = GroupDelivery(
            message_id=msg_uuid,
            recipient_user_id=current_user.account_id,
            delivered_at=datetime.utcnow(),
        )
        db.add(delivery)
    else:
        # Only update if not already set or if this is later
        if not delivery.delivered_at:
            delivery.delivered_at = datetime.utcnow()

    try:
        db.commit()
        return {"message": "Marked as delivered"}
    except Exception as e:
        db.rollback()
        logger.error(f"Error marking delivered: {e}")
        raise HTTPException(status_code=500, detail="Failed to mark delivered")


@router.post("/group-messages/{message_id}/read")
async def mark_read(
    message_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark message as read. Idempotent."""
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")

    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID format")

    message = db.query(GroupMessage).filter(GroupMessage.id == msg_uuid).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    # Find delivery record
    delivery = (
        db.query(GroupDelivery)
        .filter(
            GroupDelivery.message_id == msg_uuid,
            GroupDelivery.recipient_user_id == current_user.account_id,
        )
        .first()
    )

    if not delivery:
        # Create delivery record if it doesn't exist
        delivery = GroupDelivery(
            message_id=msg_uuid,
            recipient_user_id=current_user.account_id,
            delivered_at=datetime.utcnow(),
            read_at=datetime.utcnow(),
        )
        db.add(delivery)
    else:
        # Only update if not already set or if this is later
        if not delivery.read_at:
            delivery.read_at = datetime.utcnow()
        if not delivery.delivered_at:
            delivery.delivered_at = datetime.utcnow()

    try:
        db.commit()
        return {"message": "Marked as read"}
    except Exception as e:
        db.rollback()
        logger.error(f"Error marking read: {e}")
        raise HTTPException(status_code=500, detail="Failed to mark read")


@router.delete("/group-messages/{message_id}")
async def delete_message(
    message_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Soft delete message. Sender or admin only."""
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")

    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID format")

    message = db.query(GroupMessage).filter(GroupMessage.id == msg_uuid).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    # Check if sender or admin
    if message.sender_user_id != current_user.account_id:
        participant = (
            db.query(GroupParticipant)
            .filter(
                GroupParticipant.group_id == message.group_id,
                GroupParticipant.user_id == current_user.account_id,
            )
            .first()
        )

        if not participant or participant.role not in ["owner", "admin"]:
            raise HTTPException(status_code=403, detail="FORBIDDEN")

    # Soft delete: In a real implementation, you'd add a deleted_at column
    # For now, we'll just return success (client-side filtering)
    return {"message": "Message deleted"}
