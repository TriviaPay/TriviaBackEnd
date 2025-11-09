# E2EE QA Test Checklist

This document provides comprehensive test scenarios for the E2EE DM system, covering all edge cases and expected behaviors.

## Test Environment Setup

- Enable E2EE DM: `E2EE_DM_ENABLED=true`
- Set up test users with valid JWT tokens
- Ensure Redis is running
- Database migrations applied

## 1. Key Management Tests

### 1.1 Device Registration & Key Bundle Upload

**Test:** Upload key bundle for new device
- **Steps:**
  1. Generate identity keypair, signed prekey, and 100 one-time prekeys client-side
  2. Call `POST /e2ee/keys/upload` with bundle
  3. Verify response: `{device_id, bundle_version: 1, prekeys_stored: 100}`
- **Expected:** Device created, bundle stored, OTPKs stored
- **Verify:** `GET /e2ee/devices` shows new device

**Test:** Update existing device bundle
- **Steps:**
  1. Upload bundle for device
  2. Upload new bundle with same device_id
  3. Verify `bundle_version` increments
- **Expected:** Bundle version increases, old OTPKs cleaned up

**Test:** Upload bundle without device_id (auto-generate)
- **Steps:**
  1. Call `POST /e2ee/keys/upload` without `device_id`
  2. Verify UUID is generated and returned
- **Expected:** New device_id in response

### 1.2 Key Bundle Fetch

**Test:** Fetch bundles for user with multiple devices
- **Steps:**
  1. Register 2 devices for user A
  2. As user B, call `GET /e2ee/keys/bundle?user_id={A}`
  3. Verify both devices returned with bundles
- **Expected:** All active devices with their bundles returned

**Test:** Fetch bundles excludes revoked devices
- **Steps:**
  1. Register device for user A
  2. Revoke device
  3. Fetch bundles for user A
- **Expected:** Revoked device not in response

**Test:** Fetch bundles for blocked user
- **Steps:**
  1. User A blocks user B
  2. User B tries to fetch A's bundles
- **Expected:** `403 BLOCKED` error

### 1.3 One-Time Prekey (OTPK) Management

**Test:** Claim OTPK successfully
- **Steps:**
  1. Upload bundle with 100 OTPKs
  2. Call `POST /e2ee/prekeys/claim` with device_id and prekey_id
  3. Verify `claimed: true` response
  4. Verify prekey marked as claimed in DB
- **Expected:** OTPK claimed, `prekeys_remaining` decrements

**Test:** Claim already-claimed OTPK
- **Steps:**
  1. Claim OTPK
  2. Try to claim same OTPK again
- **Expected:** `404 Prekey not found or already claimed`

**Test:** OTPK pool exhaustion
- **Steps:**
  1. Upload bundle with 5 OTPKs
  2. Claim all 5 OTPKs
  3. Try to claim another
- **Expected:** `409 PREKEYS_EXHAUSTED` with `retry_in_seconds` and `bundle_version`

**Test:** Low watermark alert (< 5 prekeys)
- **Steps:**
  1. Upload bundle with 3 OTPKs
  2. Check metrics endpoint
- **Expected:** Alert triggered, device flagged in metrics

### 1.4 Signed Prekey Rotation

**Test:** Signed prekey rotation enforcement
- **Steps:**
  1. Upload bundle with signed prekey
  2. Wait 60+ days (or mock time)
  3. Check bundle age
- **Expected:** System flags bundle for rotation

**Test:** Bundle staleness detection
- **Steps:**
  1. Upload bundle (version 1)
  2. Upload new bundle (version 2)
  3. Try to use old bundle version
- **Expected:** `409 BUNDLE_STALE` with latest `bundle_version`

### 1.5 Identity Key Changes

**Test:** Identity key change detection
- **Steps:**
  1. Upload bundle with identity key A
  2. Upload new bundle with identity key B (same device)
  3. Check logs
- **Expected:** Identity change logged as security event

**Test:** Identity key change frequency monitoring
- **Steps:**
  1. Change identity key 3 times in one day
  2. Check metrics
- **Expected:** Alert triggered (>2/day threshold)

### 1.6 Device Revocation

**Test:** Revoke device
- **Steps:**
  1. Register device
  2. Call `POST /e2ee/devices/revoke` with device_id
  3. Verify device status = "revoked"
- **Expected:** Device marked revoked, revocation logged

**Test:** Revoked device not served in bundles
- **Steps:**
  1. Revoke device
  2. Fetch bundles for that user
- **Expected:** Revoked device excluded from response

**Test:** Encrypt to revoked device
- **Steps:**
  1. Revoke device
  2. Try to send message to that device
- **Expected:** `409 DEVICE_REVOKED` error

**Test:** List devices includes revoked
- **Steps:**
  1. Register and revoke device
  2. Call `GET /e2ee/devices`
- **Expected:** All devices returned, revoked marked with status

## 2. Messaging Tests

### 2.1 Message Sending

**Test:** Send encrypted message
- **Steps:**
  1. Create conversation
  2. Encrypt message client-side
  3. Call `POST /dm/conversations/{id}/messages`
  4. Verify message stored
- **Expected:** Message stored as ciphertext, published to Redis

**Test:** Send message with client_message_id (idempotency)
- **Steps:**
  1. Send message with client_message_id="abc123"
  2. Retry same request with same client_message_id
- **Expected:** `200 OK` with `duplicate: true`, same message_id returned

**Test:** Send message to blocked user
- **Steps:**
  1. User A blocks user B
  2. User B tries to send message to A
- **Expected:** `403 BLOCKED` error

**Test:** Rate limiting
- **Steps:**
  1. Send 20 messages in 1 minute
  2. Try to send 21st message
- **Expected:** `429 Rate limit exceeded` with `retry_in_seconds`

**Test:** Message size limit
- **Steps:**
  1. Try to send message > 64KB
- **Expected:** `400 Message exceeds maximum size`

### 2.2 Message Receiving

**Test:** Receive message via SSE
- **Steps:**
  1. Connect to `GET /dm/sse`
  2. Send message to this user
  3. Verify SSE event received
- **Expected:** Ciphertext event delivered via SSE

**Test:** Receive message via REST (polling fallback)
- **Steps:**
  1. Disable Redis (simulate outage)
  2. Send message
  3. Poll `GET /dm/conversations/{id}/messages`
- **Expected:** Message retrievable via REST

**Test:** Out-of-order message delivery
- **Steps:**
  1. Send messages 1, 2, 3
  2. Receive 3, 1, 2 (out of order)
  3. Verify client handles correctly
- **Expected:** DR handles out-of-order, messages decrypt correctly

**Test:** Duplicate message delivery
- **Steps:**
  1. Send message with client_message_id
  2. Receive same message twice (network retry)
- **Expected:** Client de-duplicates using client_message_id

### 2.3 Message History

**Test:** Fetch message history
- **Steps:**
  1. Send 10 messages
  2. Call `GET /dm/conversations/{id}/messages?limit=50`
- **Expected:** All messages returned in chronological order

**Test:** Pagination with since parameter
- **Steps:**
  1. Send 100 messages
  2. Fetch first 50
  3. Use last message_id as `since` parameter
  4. Fetch next 50
- **Expected:** Correct pagination, no duplicates

**Test:** Cursor invalidation handling
- **Steps:**
  1. Fetch messages with cursor
  2. Delete a message
  3. Try to use old cursor
- **Expected:** If cursor invalid, refetch without cursor

### 2.4 Delivery & Read Receipts

**Test:** Mark message as delivered
- **Steps:**
  1. Receive message via SSE
  2. Call `POST /dm/messages/{id}/delivered`
- **Expected:** `delivered_at` timestamp set

**Test:** Mark message as read
- **Steps:**
  1. View message in UI
  2. Call `POST /dm/messages/{id}/read`
- **Expected:** `read_at` timestamp set

**Test:** Receipt idempotency
- **Steps:**
  1. Mark message as delivered
  2. Mark as delivered again
- **Expected:** No error, timestamp unchanged or updated if later

**Test:** Receipt ordering (later timestamps win)
- **Steps:**
  1. Mark read with timestamp T1
  2. Mark read with timestamp T2 (T2 > T1)
- **Expected:** `read_at` = T2

## 3. Transport Tests

### 3.1 SSE (Server-Sent Events)

**Test:** SSE connection establishment
- **Steps:**
  1. Connect to `GET /dm/sse?token={jwt}`
  2. Verify connection established
- **Expected:** SSE stream opens, heartbeat received

**Test:** SSE heartbeat (25s interval)
- **Steps:**
  1. Connect to SSE
  2. Wait 30 seconds
  3. Verify heartbeat received
- **Expected:** Heartbeat every 25 seconds

**Test:** Token expiry mid-stream
- **Steps:**
  1. Connect with token expiring in 30s
  2. Wait for expiry
- **Expected:** Connection closed with auth_expired event

**Test:** Max concurrent streams per user (3)
- **Steps:**
  1. Open 3 SSE connections
  2. Try to open 4th
- **Expected:** `429 Maximum 3 concurrent streams allowed`

**Test:** SSE disconnect on client close
- **Steps:**
  1. Connect to SSE
  2. Close client connection
- **Expected:** Server detects disconnect, cleans up

**Test:** SSE reconnection with exponential backoff
- **Steps:**
  1. Connect to SSE
  2. Disconnect
  3. Reconnect immediately
  4. Disconnect again
  5. Verify backoff increases
- **Expected:** Reconnection delays increase exponentially

### 3.2 Redis Pub/Sub

**Test:** Message published to Redis
- **Steps:**
  1. Send message
  2. Check Redis channel
- **Expected:** Message published to `dm:user:{user_id}` and `dm:conversation:{id}`

**Test:** Redis outage handling
- **Steps:**
  1. Stop Redis
  2. Send message
  3. Check SSE heartbeat for `relay_lag=true`
- **Expected:** Message stored in DB, SSE indicates lag, client falls back to polling

**Test:** Redis reconnection
- **Steps:**
  1. Stop Redis
  2. Start Redis
  3. Verify messages resume
- **Expected:** SSE reconnects to Redis, messages flow again

### 3.3 Multi-Tab Handling

**Test:** Multiple tabs receive same message
- **Steps:**
  1. Open 2 tabs, both connected to SSE
  2. Send message to user
- **Expected:** Both tabs receive message (client should de-dup)

## 4. Storage Tests

### 4.1 Message Storage

**Test:** Ciphertext stored as BYTEA
- **Steps:**
  1. Send message
  2. Query database directly
- **Expected:** `ciphertext` column contains binary data, not plaintext

**Test:** Soft delete semantics
- **Steps:**
  1. Send message
  2. Call `DELETE /dm/messages/{id}`
  3. Query database
- **Expected:** Ciphertext still in DB, soft delete flag set

**Test:** Message retention
- **Steps:**
  1. Send message
  2. Wait for retention period (if configured)
- **Expected:** Message retained per policy

### 4.2 Pagination & Cursors

**Test:** Cursor-based pagination
- **Steps:**
  1. Send 100 messages
  2. Fetch with cursor
  3. Verify correct window
- **Expected:** Messages paginated correctly

**Test:** Cursor with deleted messages
- **Steps:**
  1. Send 10 messages
  2. Delete message 5
  3. Use cursor from message 4
- **Expected:** Pagination continues correctly

## 5. Privacy & Abuse Tests

### 5.1 Blocking

**Test:** Block user
- **Steps:**
  1. Call `POST /dm/block` with blocked_user_id
  2. Verify block created
- **Expected:** Block record created, timestamp set

**Test:** Blocked user cannot send messages
- **Steps:**
  1. User A blocks user B
  2. User B tries to send message
- **Expected:** `403 BLOCKED` error

**Test:** Blocked user cannot fetch key bundles
- **Steps:**
  1. User A blocks user B
  2. User B tries to fetch A's bundles
- **Expected:** `403 BLOCKED` error

**Test:** Unblock user
- **Steps:**
  1. Block user
  2. Call `DELETE /dm/block/{user_id}`
- **Expected:** Block removed, messaging resumes

**Test:** List blocked users
- **Steps:**
  1. Block 3 users
  2. Call `GET /dm/blocks`
- **Expected:** All 3 blocked users returned

### 5.2 Rate Limiting

**Test:** Per-user rate limit (20 msg/min)
- **Steps:**
  1. Send 20 messages in 1 minute
  2. Try 21st
- **Expected:** `429` with `retry_in_seconds`

**Test:** Per-conversation burst limit (5 msg/5s)
- **Steps:**
  1. Send 5 messages in 5 seconds to same conversation
  2. Try 6th
- **Expected:** `429` with `retry_in_seconds`

**Test:** Rate limit retry_in_seconds calculation
- **Steps:**
  1. Hit rate limit
  2. Verify `retry_in_seconds` in response
- **Expected:** Accurate retry time provided

## 6. Multi-Device Tests

### 6.1 Multi-Device Message Delivery

**Test:** Send to multiple recipient devices
- **Steps:**
  1. User A has 2 devices
  2. User B sends message
  3. Verify both devices receive
- **Expected:** Message delivered to all active devices

**Test:** Device delivery matrix
- **Steps:**
  1. Send message to user with 2 devices
  2. Check conversation details
- **Expected:** Delivery status per device visible (metadata only)

### 6.2 Device Session Management

**Test:** Per-device DR sessions
- **Steps:**
  1. User A has 2 devices
  2. User B creates sessions for both
  3. Send message
- **Expected:** Separate encryption for each device

## 7. Error Handling Tests

### 7.1 Error Response Codes

**Test:** 409 PREKEYS_EXHAUSTED
- **Steps:**
  1. Exhaust OTPK pool
  2. Try to claim another
- **Expected:** `409` with `retry_in_seconds` and `bundle_version`

**Test:** 409 BUNDLE_STALE
- **Steps:**
  1. Use old bundle version
  2. Server detects staleness
- **Expected:** `409` with latest `bundle_version`

**Test:** 409 DEVICE_REVOKED
- **Steps:**
  1. Revoke device
  2. Try to send to that device
- **Expected:** `409 DEVICE_REVOKED`

**Test:** 403 BLOCKED
- **Steps:**
  1. Block user
  2. Try to interact
- **Expected:** `403 BLOCKED`

**Test:** 429 Rate Limit
- **Steps:**
  1. Exceed rate limit
- **Expected:** `429` with `retry_in_seconds`

## 8. Observability Tests

### 8.1 Metrics Endpoint

**Test:** Admin metrics endpoint
- **Steps:**
  1. As admin, call `GET /dm/metrics`
  2. Verify metrics returned
- **Expected:** Redis status, OTPK pools, delivery stats, SSE connections

**Test:** Non-admin cannot access metrics
- **Steps:**
  1. As regular user, call `GET /dm/metrics`
- **Expected:** `403 Admin access required`

### 8.2 Monitoring

**Test:** OTPK pool distribution tracking
- **Steps:**
  1. Create devices with various OTPK counts
  2. Check metrics
- **Expected:** Distribution visible in metrics

**Test:** Message delivery latency
- **Steps:**
  1. Send message
  2. Check metrics for latency
- **Expected:** p95/p99 latency tracked

## 9. Security Tests

### 9.1 Content Privacy

**Test:** Server never sees plaintext
- **Steps:**
  1. Send message
  2. Check database, logs, Redis
- **Expected:** Only ciphertext visible, no plaintext anywhere

**Test:** Ciphertext in logs
- **Steps:**
  1. Send message
  2. Check application logs
- **Expected:** No ciphertext bodies in logs, only message IDs

### 9.2 Key Security

**Test:** Private keys never sent to server
- **Steps:**
  1. Upload key bundle
  2. Verify only public keys in request
- **Expected:** Only public keys in bundle upload

**Test:** Key backup encryption
- **Steps:**
  1. Backup keys with passphrase
  2. Check server storage
- **Expected:** Only encrypted blob stored, passphrase never sent

## 10. Integration Tests

### 10.1 End-to-End Flow

**Test:** Complete message flow
- **Steps:**
  1. User A registers device
  2. User B registers device
  3. User A fetches B's bundle
  4. User A creates session (X3DH)
  5. User A sends message
  6. User B receives via SSE
  7. User B decrypts and displays
  8. User B marks as read
- **Expected:** Complete flow works end-to-end

**Test:** Multi-device end-to-end
- **Steps:**
  1. User A has 2 devices
  2. User B sends message
  3. Both devices receive
  4. Both decrypt successfully
- **Expected:** Multi-device delivery works

## Test Completion Criteria

- [ ] All key management tests pass
- [ ] All messaging tests pass
- [ ] All transport tests pass
- [ ] All storage tests pass
- [ ] All privacy/abuse tests pass
- [ ] All multi-device tests pass
- [ ] All error handling tests pass
- [ ] All observability tests pass
- [ ] All security tests pass
- [ ] All integration tests pass

## Known Issues & Limitations

Document any known issues discovered during testing here.

