"""Messaging/Realtime schemas."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from core.config import GLOBAL_CHAT_MAX_MESSAGE_LENGTH, PRIVATE_CHAT_MAX_MESSAGE_LENGTH


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


class E2EEOneTimePrekeyRequest(BaseModel):
    prekey_pub: str = Field(
        ...,
        description="Base64 encoded one-time prekey public key",
        example="dGVzdF9wcmVrZXlfcHVibGljX2tleV8xMjM0NTY3ODkwYWJjZGVm",
    )


class E2EEUploadKeyBundleRequest(BaseModel):
    device_id: Optional[str] = Field(
        None,
        description="Device UUID (optional, will be generated if not provided)",
        example="550e8400-e29b-41d4-a716-446655440000",
    )
    device_name: str = Field(..., description="Device name/identifier", example="iPhone 15 Pro")
    identity_key_pub: str = Field(
        ...,
        description="Base64 encoded identity public key",
        example="dGVzdF9pZGVudGl0eV9wdWJsaWNfa2V5XzEyMzQ1Njc4OTBhYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5eg==",
    )
    signed_prekey_pub: str = Field(
        ...,
        description="Base64 encoded signed prekey public key",
        example="dGVzdF9zaWduZWRfcHJla2V5X3B1YmxpY19rZXlfMTIzNDU2Nzg5MGFiY2RlZmdoaWprbG1ub3BxcnN0dXZ3eHl6",
    )
    signed_prekey_sig: str = Field(
        ...,
        description="Base64 encoded signature of signed prekey",
        example="dGVzdF9zaWduYXR1cmVfb2Zfc2lnbmVkX3ByZWtleV8xMjM0NTY3ODkwYWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo=",
    )
    one_time_prekeys: List[E2EEOneTimePrekeyRequest] = Field(
        ..., description="List of one-time prekeys", min_items=1
    )


class E2EERevokeDeviceRequest(BaseModel):
    device_id: str = Field(
        ...,
        description="Device UUID to revoke",
        example="550e8400-e29b-41d4-a716-446655440000",
    )
    reason: Optional[str] = Field(
        None, description="Reason for revocation", example="Device lost or stolen"
    )


class E2EEClaimPrekeyRequest(BaseModel):
    device_id: str = Field(
        ..., description="Device UUID", example="550e8400-e29b-41d4-a716-446655440000"
    )
    prekey_id: int = Field(..., description="One-time prekey ID to claim", example=1)


class GroupCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=100, example="My Test Group")
    about: Optional[str] = Field(None, max_length=500, example="This is a test group")
    photo_url: Optional[str] = Field(None, example="https://example.com/group-photo.jpg")


class GroupUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=100, example="Updated Group Title")
    about: Optional[str] = Field(None, max_length=500, example="Updated group description")
    photo_url: Optional[str] = Field(None, example="https://example.com/new-group-photo.jpg")


class GroupInviteCreateRequest(BaseModel):
    type: str = Field(..., pattern="^(link|direct)$", example="link")
    expires_at: Optional[datetime] = Field(None, example="2025-11-12T16:00:00Z")
    max_uses: Optional[int] = Field(None, ge=1, example=10)
    target_user_id: Optional[int] = Field(None, example=1142961859)


class GroupJoinRequest(BaseModel):
    code: str = Field(..., example="ABC123XYZ")


class GroupAddMembersRequest(BaseModel):
    user_ids: List[int] = Field(..., min_items=1, example=[1142961859, 9876543210])


class GroupPromoteRequest(BaseModel):
    user_id: int = Field(..., example=1142961859)


class GroupBanRequest(BaseModel):
    user_id: int = Field(..., example=1142961859)
    reason: Optional[str] = Field(None, example="Violation of group rules")


class GroupMuteRequest(BaseModel):
    mute_until: Optional[datetime] = Field(None, example="2025-11-10T16:00:00Z")


class GroupSendMessageRequest(BaseModel):
    client_message_id: Optional[str] = Field(None, example="group_msg_1234567890")
    ciphertext: str = Field(
        ...,
        description="Base64 encoded ciphertext",
        example="dGVzdF9ncm91cF9jaXBoZXJ0ZXh0X2VuY29kZWRfaW5fYmFzZTY0X2Zvcm1hdF8xMjM0NTY3ODkwYWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo=",
    )
    proto: int = Field(..., description="Protocol type: 10=sender-key msg, 11=sender-key distribution", example=10)
    group_epoch: int = Field(..., description="Group epoch this message belongs to", example=0)
    sender_key_id: Optional[str] = Field(None, example="550e8400-e29b-41d4-a716-446655440000")
    reply_to_message_id: Optional[str] = Field(None, example="550e8400-e29b-41d4-a716-446655440000")


class PrivateChatSendMessageRequest(BaseModel):
    recipient_id: int = Field(..., description="User ID of recipient")
    message: str = Field(..., min_length=1, max_length=PRIVATE_CHAT_MAX_MESSAGE_LENGTH)
    client_message_id: Optional[str] = None
    reply_to_message_id: Optional[int] = None


class PrivateChatAcceptRejectRequest(BaseModel):
    conversation_id: int
    action: str = Field(..., description="'accept' or 'reject'")
