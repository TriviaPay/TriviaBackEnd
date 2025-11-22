# Testing Chat Mute and Push Notification Endpoints

This document describes how to test all the endpoints created after separating global chat and trivia live chat.

## Prerequisites

1. Server must be running on `http://127.0.0.1:8000`
2. You need JWT tokens for at least 2 test users
3. For push notification tests, users should have OneSignal players registered

## Quick Test Script

Use the provided bash script to test all endpoints:

```bash
./test_chat_endpoints_manual.sh <USER1_TOKEN> <USER2_TOKEN> <USER2_ID>
```

Example:
```bash
./test_chat_endpoints_manual.sh 'eyJhbGciOiJSUzI1NiIsImtpZCI6IlNLMnlvVm1sdmR4VnNFRTZJVE41QnM1Tkh2bzZoIiwidHlwIjoiSldUIn0...' 'eyJhbGciOiJSUzI1NiIsImtpZCI6IlNLMnlvVm1sdmR4VnNFRTZJVE41QnM1Tkh2bzZoIiwidHlwIjoiSldUIn0...' 1234567890
```

## Manual Testing with curl

### 1. Chat Mute Preferences Endpoints

#### Get mute preferences
```bash
curl -X GET "http://127.0.0.1:8000/chat-mute/preferences" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "accept: application/json"
```

#### Mute global chat
```bash
curl -X POST "http://127.0.0.1:8000/chat-mute/global" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"muted": true}'
```

#### Mute trivia live chat
```bash
curl -X POST "http://127.0.0.1:8000/chat-mute/trivia-live" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"muted": true}'
```

#### Mute a user for private chat
```bash
curl -X POST "http://127.0.0.1:8000/chat-mute/private/{user_id}" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"muted": true}'
```

#### List muted users
```bash
curl -X GET "http://127.0.0.1:8000/chat-mute/private" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "accept: application/json"
```

### 2. Global Chat Endpoints

#### Send message (triggers push to all users)
```bash
curl -X POST "http://127.0.0.1:8000/global-chat/send" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Test message",
    "client_message_id": "unique_id_123"
  }'
```

#### Get messages (verify avatar, frame, badge included)
```bash
curl -X GET "http://127.0.0.1:8000/global-chat/messages?limit=10" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "accept: application/json"
```

**Expected response fields:**
- `id`, `user_id`, `username`
- `profile_pic`, `avatar_url`, `frame_url`, `badge`
- `message`, `created_at`
- `online_count`

### 3. Trivia Live Chat Endpoints

#### Check status
```bash
curl -X GET "http://127.0.0.1:8000/trivia-live-chat/status" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "accept: application/json"
```

#### Send message (triggers push to all users, only if active)
```bash
curl -X POST "http://127.0.0.1:8000/trivia-live-chat/send" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Test message",
    "client_message_id": "unique_id_123"
  }'
```

#### Get messages (verify avatar, frame, badge included)
```bash
curl -X GET "http://127.0.0.1:8000/trivia-live-chat/messages?limit=10" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "accept: application/json"
```

**Expected response fields:**
- `id`, `user_id`, `username`
- `profile_pic`, `avatar_url`, `frame_url`, `badge`
- `message`, `created_at`
- `viewer_count`

### 4. Private Chat Endpoints

#### Send message (triggers push with mute check)
```bash
curl -X POST "http://127.0.0.1:8000/private-chat/send" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "recipient_id": 1234567890,
    "message": "Test message",
    "client_message_id": "unique_id_123"
  }'
```

#### List conversations (verify avatar, frame, badge included)
```bash
curl -X GET "http://127.0.0.1:8000/private-chat/conversations" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "accept: application/json"
```

**Expected response fields:**
- `conversation_id`, `peer_user_id`, `peer_username`
- `peer_profile_pic`, `peer_avatar_url`, `peer_frame_url`, `peer_badge`
- `unread_count`, `peer_online`, `peer_last_seen`

#### Get messages (verify avatar, frame, badge included)
```bash
curl -X GET "http://127.0.0.1:8000/private-chat/conversations/{conversation_id}/messages" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "accept: application/json"
```

**Expected response fields:**
- `id`, `sender_id`, `sender_username`
- `sender_profile_pic`, `sender_avatar_url`, `sender_frame_url`, `sender_badge`
- `message`, `status`, `created_at`, `is_read`

## Testing Push Notifications

Push notifications are sent asynchronously in background tasks. To verify:

1. **Check OneSignal Dashboard**: Log into your OneSignal dashboard and check the "Deliveries" section
2. **Check Server Logs**: Look for log messages like:
   - `"Sent global chat push notifications to X players"`
   - `"Sent trivia live chat push notifications to X players"`
   - `"User X is muted by Y, skipping push notification"`
   - `"User X is active, skipping push notification"`

## Testing Mute Functionality

1. **Mute global chat**, then send a global message from another user - you should NOT receive a push
2. **Mute trivia live chat**, then send a trivia message from another user - you should NOT receive a push
3. **Mute a user for private chat**, then have that user send you a message - you should NOT receive a push
4. **Unmute** and verify you receive pushes again

## Expected Behavior

### Push Notification Logic:
- ✅ Push sent if user is **not active** (last_active > 30 seconds ago)
- ✅ Push sent if user has **not muted** the chat type
- ✅ Push sent if user has **not muted** the sender (for private chat)
- ✅ Push sent if user has **registered OneSignal player**
- ❌ Push NOT sent if user is **active** (within 30 seconds)
- ❌ Push NOT sent if user has **muted** the chat type
- ❌ Push NOT sent if user has **muted** the sender (for private chat)

### Profile Data:
All chat responses should include:
- `profile_pic` / `peer_profile_pic` / `sender_profile_pic`
- `avatar_url` / `peer_avatar_url` / `sender_avatar_url` (presigned S3 URL or None)
- `frame_url` / `peer_frame_url` / `sender_frame_url` (presigned S3 URL or None)
- `badge` / `peer_badge` / `sender_badge` (dict with id, name, image_url or None)

## Troubleshooting

1. **Push notifications not sending**: 
   - Check OneSignal credentials in environment variables
   - Verify users have registered OneSignal players (`POST /onesignal/register`)
   - Check server logs for errors

2. **Mute not working**:
   - Verify mute preferences were saved (check database or use GET endpoint)
   - Check server logs for mute check logic

3. **Profile data missing**:
   - Verify users have selected avatars/frames/badges
   - Check that S3 credentials are configured for presigned URLs
   - Check server logs for presigning errors

