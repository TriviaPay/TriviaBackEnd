# Groups & Status QA Test Checklist

This document provides step-by-step test scenarios with expected API responses for all edge cases in the Groups, Status, and Presence features.

## Test Environment Setup

- Enable features: `GROUPS_ENABLED=true`, `STATUS_ENABLED=true`, `PRESENCE_ENABLED=true`
- Ensure E2EE DM is enabled: `E2EE_DM_ENABLED=true`
- Have at least 101 test users available
- Redis should be running
- Database should have all migrations applied

---

## 1. Group Membership & Roles

### Test 1.1: Group Full (100 Cap)

**Steps:**
1. Create a group
2. Add 99 members (total = 100 including creator)
3. Attempt to add 101st member

**Expected Response:**
```json
HTTP 409 Conflict
{
  "detail": "GROUP_FULL"
}
Headers: X-Capacity: 100, X-Count: 100
```

**Client Behavior:** Show "Group is full" message; do not retry automatically.

---

### Test 1.2: Concurrent Adds/Leaves

**Steps:**
1. Create group with 98 members
2. Simultaneously (within 100ms):
   - User A attempts to add User X
   - User B attempts to add User Y
   - User C attempts to add User Z

**Expected Response:**
- One request succeeds (200 OK)
- Others receive `409 GROUP_FULL` if capacity exceeded
- OR `409 Conflict` if duplicate participant detected

**Client Behavior:** If `409`, refetch members list, then retry if applicable.

---

### Test 1.3: Invite Join vs Ban Race

**Steps:**
1. Create invite code for group
2. Admin bans User X
3. User X attempts to join using valid invite code

**Expected Response:**
```json
HTTP 403 Forbidden
{
  "detail": "BANNED"
}
```

**Client Behavior:** Show "You were removed from this group" message.

---

### Test 1.4: Role Changes While Acting

**Steps:**
1. User A (admin) starts promoting User B
2. Owner demotes User A to member (in another request)
3. User A's promote request completes

**Expected Response:**
```json
HTTP 403 Forbidden
{
  "detail": "FORBIDDEN"
}
```

**Client Behavior:** Refresh group metadata, show error, reattempt if still allowed.

---

### Test 1.5: Owner Leaves

**Steps:**
1. Create group with owner and 1 admin
2. Owner attempts to leave group

**Expected Response:**
```json
HTTP 409 Conflict
{
  "detail": "OWNER_REQUIRED"
}
```

**Client Behavior:** Show owner-transfer dialog; require promoting another admin to owner first.

---

### Test 1.6: Self-Remove Last Admin

**Steps:**
1. Create group with owner and 1 admin
2. Owner attempts to demote the only admin

**Expected Response:**
```json
HTTP 409 Conflict
{
  "detail": "LAST_ADMIN"
}
```

**Client Behavior:** Require promoting another member to admin first.

---

### Test 1.7: Re-join After Leave

**Steps:**
1. User joins group via invite
2. User leaves group
3. User attempts to rejoin via same/new invite code

**Expected Response:**
- If not banned: `200 OK` with new participant record
- If banned: `403 BANNED`
- If group full: `409 GROUP_FULL`

---

## 2. Group Epoch & Rekey (Sender-Key Model)

### Test 2.1: Epoch Mismatch on Send

**Steps:**
1. User A fetches group info (epoch = 5)
2. Admin adds new member (epoch bumps to 6)
3. User A sends message with epoch = 5

**Expected Response:**
```json
HTTP 409 Conflict
{
  "detail": "EPOCH_STALE"
}
Headers: X-Error-Code: EPOCH_STALE, X-Current-Epoch: 6
```

**Client Behavior:** 
1. Fetch latest group info
2. Rotate/re-distribute sender key
3. Resend message with new epoch

---

### Test 2.2: Membership Change During Distribution

**Steps:**
1. Admin adds 5 members (epoch bumps)
2. Sender starts distributing sender-key to all members
3. While distributing, admin removes 1 member (epoch bumps again)

**Expected Response:**
- First epoch bump: `200 OK` for distribution
- Second epoch bump: `409 EPOCH_STALE` for any messages with old epoch
- Distribution envelopes for removed member should not be delivered

**Client Behavior:** 
- If distribution partially delivered, resend missing envelopes
- Track per-recipient ACKs
- Drop envelopes for removed members

---

### Test 2.3: Missing Sender-Key for a Sender

**Steps:**
1. New device joins group
2. Device attempts to send message without having distributed sender-key

**Expected Response:**
```json
HTTP 409 Conflict
{
  "detail": "SENDER_KEY_REQUIRED"
}
```

**Client Behavior:**
1. Generate sender key
2. Push distribution envelopes to all members
3. Retry send after distribution completes

---

### Test 2.4: Replay/Duplicate Distribution Messages

**Steps:**
1. Sender distributes sender-key (proto=11)
2. Same distribution message arrives again (network retry)

**Expected Response:**
- Server accepts (idempotent by `group_id`, `sender_device_id`, `group_epoch`, `sender_key_id`)
- No duplicate delivery records created

**Client Behavior:** Keep cache of seen distribution headers; ignore replays.

---

## 3. Group Messaging & Delivery

### Test 3.1: Out-of-Order/Duplicate Messages

**Steps:**
1. Send 3 messages rapidly: M1, M2, M3
2. Network delivers: M3, M1, M2 (out of order)
3. M2 arrives twice (duplicate)

**Expected Response:**
- All messages stored
- Server sorts by `(created_at, id)` for pagination
- Duplicate `client_message_id` returns existing message (200 OK)

**Client Behavior:** 
- DR/sender-key ratchet tolerates out-of-order
- De-dup UI by `client_message_id`

---

### Test 3.2: Message While Being Kicked

**Steps:**
1. User starts sending message
2. Admin removes user from group (during send)
3. Send request completes

**Expected Response:**
```json
HTTP 403 Forbidden
{
  "detail": "NOT_MEMBER"
}
```

**Client Behavior:** Show "You're no longer in this group" message.

---

### Test 3.3: Attachment Failures

**Test 3.3a: Size Too Large**
- **Steps:** Upload attachment > `GROUP_ATTACHMENT_MAX_MB` MB
- **Expected:** `413 PAYLOAD_TOO_LARGE`
- **Client:** Compress/resize, then retry

**Test 3.3b: Unsupported Media Type**
- **Steps:** Upload file with disallowed MIME type
- **Expected:** `415 UNSUPPORTED_MEDIA`
- **Client:** Show error, suggest converting file

**Test 3.3c: Daily Quota Exceeded**
- **Steps:** Upload attachments exceeding daily quota
- **Expected:** `429 QUOTA` with `retry_in_seconds`
- **Client:** Show quota limit, wait before retry

**Test 3.3d: Integrity Check**
- **Steps:** Download attachment, verify `sha256` hash
- **Expected:** Client validates hash before decrypting
- **Client:** If hash mismatch, show error, do not decrypt

---

### Test 3.4: Read/Delivery Skew

**Steps:**
1. Mark message as read (read_at = T1)
2. Network retry sends older timestamp (read_at = T0, where T0 < T1)

**Expected Response:**
- Server ignores regression (only updates if newer)
- Endpoint idempotent (safe to retry)

**Client Behavior:** Batch receipts; safe to retry.

---

## 4. Invites & Joining

### Test 4.1: Expired Invite

**Steps:**
1. Create invite with 1-hour expiry
2. Wait 2 hours
3. Attempt to join using invite code

**Expected Response:**
```json
HTTP 410 Gone
{
  "detail": "GONE"
}
```

**Client Behavior:** Show "Invite expired" message.

---

### Test 4.2: Max Uses Invite

**Steps:**
1. Create invite with `max_uses = 2`
2. User A joins (uses = 1)
3. User B joins (uses = 2)
4. User C attempts to join

**Expected Response:**
```json
HTTP 409 Conflict
{
  "detail": "MAX_USES"
}
```

**Client Behavior:** Show "Invite reached limit" message.

---

### Test 4.3: Invite Redeemed Concurrently

**Steps:**
1. Create invite with `max_uses = 1`
2. User A and User B simultaneously attempt to join

**Expected Response:**
- One succeeds (200 OK)
- Other receives `409 MAX_USES` (atomic increment)

---

### Test 4.4: Joining Closed Group

**Steps:**
1. Owner closes group
2. User attempts to join via invite code

**Expected Response:**
```json
HTTP 403 Forbidden
{
  "detail": "GROUP_CLOSED"
}
```

---

### Test 4.5: Invite to Banned User

**Steps:**
1. Admin creates invite code
2. Admin bans User X
3. User X attempts to join using invite code

**Expected Response:**
```json
HTTP 403 Forbidden
{
  "detail": "BANNED"
}
```

---

## 5. Presence (Online/Last Seen)

### Test 5.1: Privacy Settings - Everyone

**Steps:**
1. User A sets `share_last_seen = "everyone"`
2. User B (any user) queries presence for User A

**Expected Response:**
```json
HTTP 200 OK
{
  "presence": [{
    "user_id": <A>,
    "last_seen_at": "2024-01-15T12:00:00Z",
    "device_online": true
  }]
}
```

---

### Test 5.2: Privacy Settings - Contacts Only

**Steps:**
1. User A sets `share_last_seen = "contacts"`
2. User B (not a contact) queries presence for User A
3. User C (contact via DM) queries presence for User A

**Expected Response:**
- User B: `last_seen_at = null`, `device_online = false`
- User C: Actual `last_seen_at` and `device_online` values

---

### Test 5.3: Privacy Settings - Nobody

**Steps:**
1. User A sets `share_last_seen = "nobody"`
2. Any user queries presence for User A

**Expected Response:**
```json
{
  "presence": [{
    "user_id": <A>,
    "last_seen_at": null,
    "device_online": false
  }]
}
```

**Client Behavior:** Gray out presence; don't infer from message timings.

---

### Test 5.4: SSE Disconnect Storms

**Steps:**
1. User connects to SSE
2. Simulate network issues (disconnect/reconnect rapidly)
3. Monitor heartbeat behavior

**Expected Response:**
- Heartbeat every 25s
- Close after 2 missed heartbeats
- Mark `last_seen_at` on disconnect

**Client Behavior:** Exponential backoff with jitter; resume cursor on reconnect.

---

### Test 5.5: Clock Skew

**Steps:**
1. Client clock is 5 minutes ahead
2. Client sends message with client timestamp
3. Server stores message

**Expected Response:**
- Server always uses server `created_at` timestamp
- Client displays server time for ordering

**Client Behavior:** Display "last seen" from server time, not client time.

---

## 6. Status (24h Stories)

### Test 6.1: Audience Expansion/Shrink

**Steps:**
1. User A creates status post with audience = contacts (10 people)
2. User A adds 5 new contacts
3. User A creates new post (should include new contacts)

**Expected Response:**
- First post: `post_epoch = 0`, audience = 10
- Second post: `post_epoch = 1` (if epoch bumped), audience = 15
- Feed includes `epoch` in response

**Client Behavior:** 
- If can't decrypt with old epoch, fetch new key notice
- Hide post until key arrives

---

### Test 6.2: Expired Content

**Steps:**
1. Create status post (TTL = 24h)
2. Wait 25 hours
3. Query status feed

**Expected Response:**
- `GET /status/feed` excludes expired posts
- Direct fetch of expired post: `410 Gone`

**Client Behavior:** Purge local copies past TTL.

---

### Test 6.3: Views Privacy

**Steps:**
1. User A creates status post
2. User A sets `read_receipts = false`
3. User B views the post
4. User A queries view count

**Expected Response:**
- Server accepts view (stores in DB)
- `GET /status/posts/{id}` does not expose viewer list when `read_receipts = false`

**Client Behavior:** Respect creator's setting; don't show viewer list when disabled.

---

### Test 6.4: Attachment Too Large / Throttling

**Test 6.4a: Size Too Large**
- **Steps:** Upload attachment > `STATUS_ATTACHMENT_MAX_MB` MB
- **Expected:** `413 PAYLOAD_TOO_LARGE`
- **Client:** Compress/resize, then retry

**Test 6.4b: Daily Limit**
- **Steps:** Create > `STATUS_MAX_POSTS_PER_DAY` posts in one day
- **Expected:** `429` with `retry_in_seconds` (next day)
- **Client:** Show limit, wait before retry

---

## 7. Multi-Device & Sync

### Test 7.1: New Device Bootstrapping

**Steps:**
1. User has 2 devices (Device A, Device B)
2. User joins group on Device A
3. User registers Device C
4. Device C needs to decrypt group history

**Expected Response:**
- `GET /groups/{id}/messages` returns recent sender-key distribution messages (proto=11)
- Device C can fetch distribution history

**Client Behavior:** 
1. Fetch distribution history
2. Decrypt old group messages using fetched sender keys

---

### Test 7.2: Read Receipt Sync Across Self-Devices

**Steps:**
1. User has 2 devices (Device A, Device B)
2. User reads message on Device A
3. Device B should show message as read

**Expected Response:**
- Server accepts duplicate read-ups
- Stores max timestamp
- Both devices show read status

**Client Behavior:** 
- Send encrypted "read-up-to" control message to self-devices
- Reconcile on startup

---

### Test 7.3: Device Revocation Mid-Session

**Steps:**
1. User has Device A and Device B
2. User revokes Device A
3. Device A attempts to send group message

**Expected Response:**
```json
HTTP 409 Conflict
{
  "detail": "DEVICE_REVOKED"
}
```

**Client Behavior (Peers):** Drop sessions to revoked device on next safety notice.

---

## 8. Rate Limiting & Abuse Controls

### Test 8.1: Per-User Message Rate (Group)

**Steps:**
1. Send `GROUP_MESSAGE_RATE_PER_USER_PER_MIN` messages in 1 minute
2. Attempt to send 1 more

**Expected Response:**
```json
HTTP 429 Too Many Requests
{
  "detail": "Rate limit exceeded..."
}
Headers: X-Retry-After: 15, X-RateLimit-Limit: 40, X-RateLimit-Remaining: 0
```

**Client Behavior:** Show "Too fast" message; pause sending.

---

### Test 8.2: Per-Group Burst

**Steps:**
1. Send `GROUP_BURST_PER_5S` messages in 5 seconds
2. Attempt to send 1 more

**Expected Response:**
```json
HTTP 429 Too Many Requests
Headers: X-Retry-After: 3, X-RateLimit-Limit: 10
```

---

### Test 8.3: Invite Abuse

**Steps:**
1. Rapidly create 50 invite codes for same group
2. Monitor for throttling

**Expected Response:**
- After threshold: `429` with `retry_in_seconds`
- Anomaly alerts triggered

---

### Test 8.4: Spam Reports (E2EE)

**Steps:**
1. User receives spam message
2. User sends encrypted report token + plaintext category

**Expected Response:**
- `200 OK` - Report accepted
- Server never inspects content
- Throttle or auto-block by policy (metadata only)

---

## 9. Pagination & Cursors

### Test 9.1: Invalid/Expired Cursor

**Steps:**
1. Fetch messages with valid cursor
2. Message with cursor ID is deleted
3. Use same cursor again

**Expected Response:**
```json
HTTP 400 Bad Request
{
  "detail": "INVALID_CURSOR"
}
```

**Client Behavior:** Drop cursor, re-query without cursor to re-anchor.

---

### Test 9.2: Deletions and Reordering

**Steps:**
1. Fetch 10 messages (M1-M10)
2. Delete M5
3. Fetch next page using cursor from M4

**Expected Response:**
- Deletions don't invalidate cursors
- Ordering is `(created_at, id)` stable
- Next page returns M6-M15 (M5 missing is expected)

**Client Behavior:** Tolerant to holes in history.

---

## 10. Consistency & Transactions

### Test 10.1: Double-Writes (Message + Delivery)

**Steps:**
1. Send group message
2. Monitor database transaction

**Expected Response:**
- Message written in transaction
- Delivery records created in same transaction
- Redis publish happens AFTER commit only

---

### Test 10.2: Epoch Bump + Message Race

**Steps:**
1. Admin adds member (triggers epoch bump)
2. User sends message with old epoch (during bump)

**Expected Response:**
- Epoch bump happens first (transaction)
- Message with old epoch: `409 EPOCH_STALE`

---

### Test 10.3: Add/Remove + Cap Check

**Steps:**
1. Group has 99 members
2. Admin attempts to add 2 members simultaneously

**Expected Response:**
- Cap check inside transaction
- One succeeds, other: `409 GROUP_FULL`

---

## 11. SSE/Redis Resilience

### Test 11.1: Redis Publish Failure

**Steps:**
1. Stop Redis
2. Send group message
3. Check response and SSE heartbeat

**Expected Response:**
- Message persists in database (200 OK)
- SSE heartbeat includes: `relay_lag: true`, `redis_status: "unavailable"`

**Client Behavior:** Fall back to polling `GET /groups/{id}/messages?after=cursor` every 3-10s while lag is true.

---

### Test 11.2: Multiple Subscriptions Growth

**Steps:**
1. User joins 100 groups
2. User connects to SSE
3. Monitor subscription count

**Expected Response:**
- Server subscribes to all group channels
- If pathological (e.g., 1000+ groups), may cap or require polling

---

### Test 11.3: Hot Partition / Massive Group

**Steps:**
1. Create group with 100 members
2. All members send typing indicators simultaneously
3. Monitor Redis performance

**Expected Response:**
- Coalesce typing events (min interval 1.5s)
- Throttle sender-key distributions if needed

---

## 12. Admin/Ops & Retention

### Test 12.1: Legal Hold

**Steps:**
1. Admin pins group for legal hold
2. Attempt to delete group messages

**Expected Response:**
- TTL deletes suspended for pinned groups
- Messages retained beyond normal TTL

---

### Test 12.2: GDPR/Right-to-Erasure

**Steps:**
1. User requests data deletion
2. Admin processes deletion

**Expected Response:**
- Server copies (ciphertext + metadata) deleted
- Peers may retain their copies (documented behavior)

---

### Test 12.3: Metrics Sanity

**Steps:**
1. Monitor metrics endpoint
2. Check for anomalies

**Expected Alerts:**
- Redis lag > 2s for 60s+
- Epoch churn spikes
- Invite redemption spikes
- OTPK shortages
- SSE auth-expired spikes
- DB p99 > 200ms

---

## 13. Error Code Reference

| Code | Status | Detail | When |
|------|--------|--------|------|
| `NOT_MEMBER` | 403 | User not a member | Posting to group without membership |
| `BANNED` | 403 | User banned | Banned user attempts action |
| `GROUP_CLOSED` | 403 | Group is closed | Join/action on closed group |
| `GROUP_FULL` | 409 | Group at capacity | Add member when at 100 |
| `EPOCH_STALE` | 409 | Epoch mismatch | Send with old epoch |
| `SENDER_KEY_REQUIRED` | 409 | Sender key missing | Send without distribution |
| `LAST_ADMIN` | 409 | Last admin | Demote/remove last admin |
| `OWNER_REQUIRED` | 409 | Owner required | Owner attempts to leave |
| `MAX_USES` | 409 | Invite exhausted | Join with maxed invite |
| `GONE` | 410 | Expired | Expired invite/post |
| `PAYLOAD_TOO_LARGE` | 413 | Attachment too large | File exceeds limit |
| `UNSUPPORTED_MEDIA` | 415 | Invalid MIME type | Disallowed file type |
| `RATE_LIMITED` | 429 | Rate limit exceeded | Too many requests |
| `INVALID_CURSOR` | 400 | Bad cursor | Invalid pagination cursor |
| `INVALID_INVITE` | 400 | Bad invite code | Invalid/expired code |
| `RELAY_UNAVAILABLE` | 503 | Redis/stream down | Redis outage |

---

## Test Execution Notes

1. **Run tests in isolated environment** to avoid side effects
2. **Reset database** between test suites if needed
3. **Monitor logs** for unexpected errors
4. **Verify Redis** is running for SSE tests
5. **Check metrics endpoint** after each test suite
6. **Document any deviations** from expected behavior

---

## Sign-off Checklist

- [ ] All membership edge cases tested
- [ ] Epoch/rekey flows verified
- [ ] Invite scenarios covered
- [ ] Presence privacy validated
- [ ] Status TTL and audience tested
- [ ] Multi-device sync verified
- [ ] Rate limiting confirmed
- [ ] Redis resilience tested
- [ ] Error codes match specification
- [ ] Metrics and alerts configured

