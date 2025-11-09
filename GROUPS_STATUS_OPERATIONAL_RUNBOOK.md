# Groups & Status Operational Runbook

This document provides step-by-step procedures for on-call engineers to handle edge cases, incidents, and troubleshooting scenarios for Groups, Status, and Presence features.

---

## Quick Reference: Error Codes

| Code | HTTP | Meaning | Action |
|------|------|---------|--------|
| `GROUP_FULL` | 409 | Group at 100 members | Inform user, no auto-retry |
| `EPOCH_STALE` | 409 | Client epoch outdated | Client must refetch and resend |
| `BANNED` | 403 | User banned from group | Expected behavior |
| `NOT_MEMBER` | 403 | User not in group | Check membership status |
| `SENDER_KEY_REQUIRED` | 409 | Missing sender key | Client must distribute first |
| `MAX_USES` | 409 | Invite exhausted | Create new invite |
| `GONE` | 410 | Expired invite/post | Expected for expired content |
| `RATE_LIMITED` | 429 | Too many requests | Check `X-Retry-After` header |

---

## Incident Response Procedures

### 1. Group Membership Issues

#### Symptom: Users cannot join groups (409 GROUP_FULL)

**Investigation:**
```sql
-- Check group capacity
SELECT 
    g.id, 
    g.title, 
    COUNT(gp.user_id) as current_count,
    g.max_participants,
    g.group_epoch
FROM groups g
LEFT JOIN group_participants gp ON g.id = gp.group_id AND gp.is_banned = false
WHERE g.id = '<group_id>'
GROUP BY g.id;
```

**Resolution:**
- If count < 100: Check for race condition, verify unique constraint
- If count = 100: Expected behavior, inform user
- If count > 100: **DATA CORRUPTION** - escalate immediately

**Prevention:**
- Monitor for groups approaching capacity
- Alert when group reaches 95 members

---

#### Symptom: Concurrent add/remove causing duplicates

**Investigation:**
```sql
-- Check for duplicate participants
SELECT group_id, user_id, COUNT(*) 
FROM group_participants 
GROUP BY group_id, user_id 
HAVING COUNT(*) > 1;
```

**Resolution:**
- If duplicates found: Run cleanup script (keep most recent)
- Verify unique constraint exists: `uq_group_participants_group_user`
- Check transaction isolation level

**Prevention:**
- Ensure unique constraint is enforced
- Use database transactions for all membership changes

---

#### Symptom: Owner leaves group (409 OWNER_REQUIRED)

**Investigation:**
```sql
-- Check group ownership
SELECT g.id, g.title, gp.user_id, gp.role
FROM groups g
JOIN group_participants gp ON g.id = gp.group_id
WHERE g.id = '<group_id>' AND gp.role = 'owner';
```

**Resolution:**
- If no owner: **CRITICAL** - Group is orphaned
  1. Find most senior admin (by `joined_at`)
  2. Manually promote to owner via SQL:
     ```sql
     UPDATE group_participants 
     SET role = 'owner' 
     WHERE group_id = '<group_id>' AND user_id = <admin_user_id>;
     ```
  3. Notify user
- If owner exists: Verify endpoint logic is blocking correctly

**Prevention:**
- Require at least 1 admin before owner can leave
- Add monitoring for groups without owners

---

### 2. Epoch & Rekey Issues

#### Symptom: Messages failing with 409 EPOCH_STALE

**Investigation:**
```sql
-- Check epoch distribution
SELECT 
    g.id,
    g.group_epoch,
    COUNT(DISTINCT gm.group_epoch) as message_epochs,
    MIN(gm.created_at) as oldest_message,
    MAX(gm.created_at) as newest_message
FROM groups g
LEFT JOIN group_messages gm ON g.id = gm.group_id
WHERE g.id = '<group_id>'
GROUP BY g.id, g.group_epoch;
```

**Resolution:**
- If epoch mismatch: Normal during membership changes
  - Client should refetch group info
  - Client should redistribute sender keys
- If persistent: Check for stuck epoch bumps
  - Verify `increment_group_epoch()` is called correctly
  - Check for transaction rollbacks

**Prevention:**
- Monitor epoch churn rate
- Alert if epoch changes > 10/hour for single group

---

#### Symptom: Missing sender keys (409 SENDER_KEY_REQUIRED)

**Investigation:**
```sql
-- Check sender key distribution
SELECT 
    gsk.group_id,
    gsk.sender_user_id,
    gsk.sender_device_id,
    gsk.group_epoch,
    gsk.sender_key_id,
    gsk.rotated_at
FROM group_sender_keys gsk
WHERE gsk.group_id = '<group_id>' 
  AND gsk.group_epoch = <current_epoch>;
```

**Resolution:**
- If no sender key: Client must generate and distribute
- If sender key exists but client missing: Check distribution messages
  ```sql
  SELECT * FROM group_messages 
  WHERE group_id = '<group_id>' 
    AND proto = 11  -- sender-key distribution
    AND sender_device_id = '<device_id>'
  ORDER BY created_at DESC LIMIT 10;
  ```

**Prevention:**
- Monitor sender key distribution success rate
- Alert if distribution failures > 5%

---

### 3. Invite Issues

#### Symptom: Invites not working (410 GONE or 409 MAX_USES)

**Investigation:**
```sql
-- Check invite status
SELECT 
    gi.id,
    gi.code,
    gi.expires_at,
    gi.max_uses,
    gi.uses,
    gi.created_at,
    CASE 
        WHEN gi.expires_at < NOW() THEN 'expired'
        WHEN gi.max_uses IS NOT NULL AND gi.uses >= gi.max_uses THEN 'maxed'
        ELSE 'active'
    END as status
FROM group_invites gi
WHERE gi.code = '<invite_code>';
```

**Resolution:**
- If expired: Expected behavior, create new invite
- If maxed: Expected behavior, create new invite
- If active but failing: Check group status (closed/full)

**Prevention:**
- Set reasonable expiry times (default 72h)
- Monitor invite redemption rate
- Alert on invite abuse patterns

---

#### Symptom: Concurrent invite redemption

**Investigation:**
```sql
-- Check for race conditions
SELECT code, uses, max_uses, created_at
FROM group_invites
WHERE code = '<invite_code>'
FOR UPDATE;  -- Lock row
```

**Resolution:**
- Verify atomic increment in code
- Check transaction isolation
- If uses > max_uses: Data corruption, investigate

**Prevention:**
- Use database transactions
- Use `SELECT FOR UPDATE` for invite redemption

---

### 4. Presence Issues

#### Symptom: Presence not updating (last_seen_at stale)

**Investigation:**
```sql
-- Check presence records
SELECT 
    up.user_id,
    up.last_seen_at,
    up.device_online,
    up.privacy_settings
FROM user_presence up
WHERE up.user_id = <user_id>;
```

**Resolution:**
- If `last_seen_at` old: Check SSE connection
  - Verify user is connected to `/dm/sse`
  - Check heartbeat frequency (should be 25s)
  - Verify heartbeat updates `last_seen_at`
- If `device_online` stuck: Check disconnect cleanup
  - Verify `finally` block in SSE sets `device_online = false`

**Prevention:**
- Monitor SSE connection health
- Alert if heartbeat misses > 2
- Track presence update frequency

---

#### Symptom: Privacy settings not respected

**Investigation:**
```sql
-- Check privacy settings
SELECT 
    up.user_id,
    up.privacy_settings->>'share_last_seen' as share_last_seen,
    up.privacy_settings->>'share_online' as share_online
FROM user_presence up
WHERE up.user_id = <user_id>;
```

**Resolution:**
- Verify privacy check in `GET /status/presence` endpoint
- Check if settings are being applied correctly
- Test with different privacy levels

**Prevention:**
- Add unit tests for privacy logic
- Monitor privacy setting changes

---

### 5. Status Post Issues

#### Symptom: Status posts not appearing in feed

**Investigation:**
```sql
-- Check status posts
SELECT 
    sp.id,
    sp.owner_user_id,
    sp.created_at,
    sp.expires_at,
    sp.audience_mode,
    COUNT(sa.viewer_user_id) as audience_count
FROM status_posts sp
LEFT JOIN status_audience sa ON sp.id = sa.post_id
WHERE sp.owner_user_id = <user_id>
GROUP BY sp.id
ORDER BY sp.created_at DESC;
```

**Resolution:**
- If expired: Expected (TTL = 24h), check `expires_at`
- If not in audience: Check `status_audience` table
  ```sql
  SELECT * FROM status_audience 
  WHERE post_id = '<post_id>' AND viewer_user_id = <viewer_id>;
  ```
- If epoch mismatch: Check `post_epoch` changes

**Prevention:**
- Monitor status post creation rate
- Alert on TTL cleanup failures
- Track audience expansion/shrinkage

---

#### Symptom: Status views not recording

**Investigation:**
```sql
-- Check view records
SELECT 
    sv.post_id,
    sv.viewer_user_id,
    sv.viewed_at,
    sp.owner_user_id
FROM status_views sv
JOIN status_posts sp ON sv.post_id = sp.id
WHERE sv.post_id = '<post_id>';
```

**Resolution:**
- If no views: Check if privacy allows views
  - Verify `read_receipts` setting
  - Check if viewer is in audience
- If views missing: Check endpoint logic
  - Verify idempotency
  - Check transaction handling

**Prevention:**
- Monitor view recording rate
- Track privacy setting impact on views

---

### 6. Redis/SSE Resilience

#### Symptom: Redis publish failures

**Investigation:**
```bash
# Check Redis status
redis-cli ping

# Check Redis connection
redis-cli info stats
```

**Resolution:**
1. **If Redis down:**
   - Messages still persist in database (check `group_messages` table)
   - SSE heartbeat shows `relay_lag: true`
   - Clients should fall back to polling
   - Restart Redis service
   - Verify reconnection

2. **If Redis slow:**
   - Check Redis memory usage
   - Check for slow commands
   - Consider Redis cluster

**Prevention:**
- Monitor Redis latency (alert if > 2s)
- Set up Redis health checks
- Implement circuit breaker pattern

---

#### Symptom: SSE connection storms

**Investigation:**
```python
# Check active connections (from code)
_active_dm_sse_connections  # Dict of user_id -> set of connection IDs
```

**Resolution:**
- If too many connections per user:
  - Check `E2EE_DM_MAX_CONCURRENT_STREAMS_PER_USER` (default: 3)
  - Verify connection cleanup in `finally` block
  - Check for connection leaks

**Prevention:**
- Monitor connection count per user
- Alert if user has > 5 concurrent connections
- Implement connection cleanup job

---

### 7. Rate Limiting Issues

#### Symptom: Legitimate users hitting rate limits

**Investigation:**
```sql
-- Check message rate
SELECT 
    sender_user_id,
    COUNT(*) as message_count,
    MIN(created_at) as first_message,
    MAX(created_at) as last_message
FROM group_messages
WHERE created_at >= NOW() - INTERVAL '1 minute'
GROUP BY sender_user_id
HAVING COUNT(*) >= 40;  -- GROUP_MESSAGE_RATE_PER_USER_PER_MIN
```

**Resolution:**
- If legitimate: Consider increasing rate limits
  - Update `GROUP_MESSAGE_RATE_PER_USER_PER_MIN` in config
  - Restart application
- If abuse: Verify rate limiting is working
  - Check rate limit headers in response
  - Verify `X-Retry-After` is accurate

**Prevention:**
- Monitor rate limit hit rate
- Alert on rate limit spikes
- Consider per-user rate limit adjustments

---

### 8. Database Performance

#### Symptom: Slow queries (p99 > 200ms)

**Investigation:**
```sql
-- Check slow queries (PostgreSQL)
SELECT 
    query,
    calls,
    total_time,
    mean_time,
    max_time
FROM pg_stat_statements
WHERE mean_time > 0.2  -- 200ms
ORDER BY mean_time DESC
LIMIT 10;
```

**Resolution:**
- Check index usage:
  ```sql
  -- Verify indices exist
  SELECT indexname, indexdef 
  FROM pg_indexes 
  WHERE tablename IN ('group_messages', 'group_participants', 'status_posts');
  ```
- Add missing indices if needed
- Consider query optimization
- Check for table bloat

**Prevention:**
- Monitor query performance metrics
- Set up slow query logging
- Regular index maintenance

---

### 9. Data Integrity Issues

#### Symptom: Orphaned records

**Investigation:**
```sql
-- Check for orphaned delivery records
SELECT gd.* 
FROM group_delivery gd
LEFT JOIN group_messages gm ON gd.message_id = gm.id
WHERE gm.id IS NULL;

-- Check for orphaned audience records
SELECT sa.* 
FROM status_audience sa
LEFT JOIN status_posts sp ON sa.post_id = sp.id
WHERE sp.id IS NULL;
```

**Resolution:**
- If orphaned records found:
  1. Investigate cause (cascade deletes missing?)
  2. Clean up orphaned records
  3. Add foreign key constraints if missing
  4. Add cascade deletes if appropriate

**Prevention:**
- Verify foreign key constraints exist
- Add database constraints
- Regular data integrity checks

---

### 10. Emergency Procedures

#### Hotfix Rollback

**Steps:**
1. Disable feature flags:
   ```bash
   export GROUPS_ENABLED=false
   export STATUS_ENABLED=false
   export PRESENCE_ENABLED=false
   ```
2. Restart application
3. Verify endpoints return 403
4. Keep database intact (don't drop tables)

**Rollback Verification:**
```bash
curl -H "Authorization: Bearer <token>" \
  https://api.example.com/groups
# Expected: 403 Forbidden
```

---

#### Database Migration Rollback

**Steps:**
1. **DO NOT** drop tables if feature is live
2. If safe, run downgrade:
   ```bash
   alembic downgrade a1b2c3d4e5f6  # Previous revision
   ```
3. Verify tables removed
4. Restart application

**Warning:** Only rollback if no production data exists in new tables.

---

#### Redis Outage Recovery

**Steps:**
1. Verify Redis is back online:
   ```bash
   redis-cli ping
   ```
2. Check application logs for reconnection
3. Verify SSE connections resume
4. Check `relay_lag` in SSE heartbeat (should be `false`)
5. Monitor message delivery

**Recovery Verification:**
```bash
# Check metrics endpoint
curl -H "Authorization: Bearer <admin_token>" \
  https://api.example.com/groups/metrics
# Verify redis_status: "available"
```

---

## Monitoring & Alerts

### Key Metrics to Monitor

1. **Group Metrics:**
   - Total groups, active groups
   - Average group size
   - Messages per hour
   - Epoch churn rate
   - Sender key distribution count

2. **Status Metrics:**
   - Posts per day
   - Active posts
   - Views per day
   - Average audience size

3. **Presence Metrics:**
   - SSE connections
   - Heartbeat success rate
   - Last seen update frequency

4. **Infrastructure:**
   - Redis latency
   - Database query p95/p99
   - SSE connection count
   - Rate limit hits

### Alert Thresholds

| Metric | Threshold | Action |
|--------|-----------|--------|
| Redis lag | > 2s for 60s | Check Redis health |
| Epoch churn | > 10/hour per group | Investigate membership changes |
| DB p99 | > 200ms | Check slow queries |
| SSE auth expired | Spike > 10/min | Check JWT expiry |
| Group full | 95+ members | Warn approaching limit |
| Invite redemption | Spike > 50/hour | Check for abuse |

---

## Troubleshooting Commands

### Database Queries

```sql
-- Find groups without owners
SELECT g.id, g.title 
FROM groups g
LEFT JOIN group_participants gp ON g.id = gp.group_id AND gp.role = 'owner'
WHERE gp.user_id IS NULL;

-- Find groups with epoch issues
SELECT g.id, g.group_epoch, COUNT(DISTINCT gm.group_epoch) as message_epochs
FROM groups g
JOIN group_messages gm ON g.id = gm.group_id
GROUP BY g.id, g.group_epoch
HAVING COUNT(DISTINCT gm.group_epoch) > 1;

-- Find expired status posts
SELECT COUNT(*) 
FROM status_posts 
WHERE expires_at < NOW();

-- Find users with many groups
SELECT user_id, COUNT(*) as group_count
FROM group_participants
WHERE is_banned = false
GROUP BY user_id
ORDER BY group_count DESC
LIMIT 10;
```

### Redis Commands

```bash
# Check Redis memory
redis-cli info memory

# Check connected clients
redis-cli client list

# Monitor pub/sub activity
redis-cli monitor

# Check specific channel
redis-cli PUBSUB CHANNELS "grp:*"
```

---

## Escalation Path

1. **Level 1 (On-Call):** Use this runbook
2. **Level 2 (Team Lead):** If issue persists > 30min or data corruption
3. **Level 3 (Engineering Manager):** If service down or security issue
4. **Level 4 (CTO/VP):** If customer data at risk

---

## Post-Incident Checklist

After resolving an incident:

- [ ] Document root cause
- [ ] Update this runbook with new procedures
- [ ] Add monitoring/alerting if missing
- [ ] Create follow-up tasks for prevention
- [ ] Update QA checklist with new test cases
- [ ] Notify stakeholders if user-facing

---

## Contact Information

- **On-Call Rotation:** [Link to PagerDuty/OpsGenie]
- **Engineering Slack:** #groups-status-oncall
- **Escalation:** [Contact info]

---

**Last Updated:** 2024-01-15
**Version:** 1.0

