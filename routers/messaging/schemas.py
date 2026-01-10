"""Messaging/Realtime schemas."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from config import GLOBAL_CHAT_MAX_MESSAGE_LENGTH


class ToggleMuteRequest(BaseModel):
    muted: bool = True


class UpdatePresenceRequest(BaseModel):
    share_last_seen: Optional[str] = Field(
        None, pattern="^(everyone|all|contacts|nobody)$", example="contacts"
    )
    share_online: Optional[bool] = Field(None, example=True)
    read_receipts: Optional[bool] = Field(None, example=True)

    class Config:
        json_schema_extra = {
            "example": {
                "share_last_seen": "contacts",
                "share_online": True,
                "read_receipts": True,
            }
        }


class SendMessageRequest(BaseModel):
    client_message_id: Optional[str] = Field(
        None, description="Client-provided ID for idempotency", example="msg_1234567890"
    )
    ciphertext: str = Field(
        ...,
        description="Base64 encoded ciphertext",
        example="dGVzdF9jaXBoZXJ0ZXh0X2VuY29kZWRfaW5fYmFzZTY0X2Zvcm1hdF8xMjM0NTY3ODkwYWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo=",
    )
    proto: int = Field(
        ..., description="Protocol type: 1=DR message, 2=PreKey message", example=1
    )
    recipient_device_ids: Optional[list] = Field(
        None,
        description="Optional list of recipient device IDs",
        example=["550e8400-e29b-41d4-a716-446655440000"],
    )


class SendMessageResponse(BaseModel):
    message_id: str
    created_at: str
    duplicate: bool


class DMMessageEnvelope(BaseModel):
    id: str
    sender_user_id: int
    sender_device_id: str
    ciphertext: str
    proto: int
    created_at: str
    client_message_id: Optional[str] = None


class GetMessagesResponse(BaseModel):
    messages: List[DMMessageEnvelope]


class MarkDeliveredResponse(BaseModel):
    message_id: str
    delivered_at: str


class MarkReadResponse(BaseModel):
    message_id: str
    read_at: str


class CreateStatusPostRequest(BaseModel):
    media_meta: dict = Field(
        ...,
        description="Encrypted media metadata (JSON)",
        example={
            "url": "https://example.com/media.jpg",
            "size": 1024000,
            "mime": "image/jpeg",
        },
    )
    audience_mode: str = Field(..., pattern="^(contacts|custom)$", example="contacts")
    custom_audience: Optional[List[int]] = Field(
        None,
        description="Custom user IDs if audience_mode='custom'",
        example=[1142961859, 9876543210],
    )


class MarkViewedRequest(BaseModel):
    post_ids: List[str] = Field(..., min_items=1)


class GlobalChatSendMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=GLOBAL_CHAT_MAX_MESSAGE_LENGTH)
    client_message_id: Optional[str] = None
    reply_to_message_id: Optional[int] = None


class GlobalChatMessageResponse(BaseModel):
    id: int
    user_id: int
    username: str
    profile_pic: Optional[str] = None
    avatar_url: Optional[str] = None
    frame_url: Optional[str] = None
    badge: Optional[dict] = None
    message: str
    created_at: str
    reply_to: Optional[dict] = None
    level: int = 1
    level_progress: str = "0/100"


class GlobalChatMessagesResponse(BaseModel):
    messages: List[GlobalChatMessageResponse]
    online_count: int


class GlobalChatCleanupResponse(BaseModel):
    deleted_count: int
    cutoff_date: str


class GlobalChatSendResponse(BaseModel):
    message_id: int
    created_at: str
    duplicate: bool


class PrivateChatBlockUserRequest(BaseModel):
    blocked_user_id: int = Field(..., description="User ID to block")


class DMBlockUserRequest(BaseModel):
    blocked_user_id: int = Field(..., description="User ID to block", example=1142961859)


class DMCreateConversationRequest(BaseModel):
    peer_user_id: int = Field(..., description="User ID of the peer user", example=1142961859)
