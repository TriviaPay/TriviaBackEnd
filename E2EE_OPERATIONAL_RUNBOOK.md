# E2EE Operational Runbook

This document provides step-by-step operational procedures for managing the E2EE DM system in production.

## Table of Contents

1. [Redis Outage Procedures](#redis-outage-procedures)
2. [Rollback/Hotfix Procedures](#rollbackhotfix-procedures)
3. [Stuck Device Remediation](#stuck-device-remediation)
4. [Abuse Burst Response](#abuse-burst-response)
5. [Monitoring and Alerting Setup](#monitoring-and-alerting-setup)
6. [Incident Response](#incident-response)

## Redis Outage Procedures

### Detection

**Symptoms:**
- SSE heartbeat shows `relay_lag=true`
- `/dm/metrics` shows `redis_status: unavailable`
- Messages stored in DB but not delivered via SSE
- High error rate in logs: "Redis unavailable"

**Verification:**
```bash
# Check Redis connection
curl -X GET "https://api.example.com/dm/metrics" \
  -H "Authorization: Bearer ADMIN_TOKEN"

# Check application logs
grep -i "redis" /var/log/app.log | tail -20
```

### Immediate Actions

1. **Verify Redis Status**
   ```bash
   # Check if Redis is actually down
   redis-cli ping
   # Expected: PONG
   ```

2. **Check Application Fallback**
   - Verify messages are still being stored in PostgreSQL
   - Confirm SSE endpoints are indicating `relay_lag=true` in heartbeats
   - Check that clients are falling back to polling mode

3. **Notify Team**
   - Post in incident channel: "Redis outage detected, messages stored but not pushed"
   - Update status page if applicable

### Recovery Steps

1. **Restart Redis** (if crashed)
   ```bash
   sudo systemctl restart redis
   # Or
   docker restart redis
   ```

2. **Verify Redis Health**
   ```bash
   redis-cli ping
   redis-cli info stats
   ```

3. **Monitor Recovery**
   ```bash
   # Watch metrics endpoint
   watch -n 5 'curl -s "https://api.example.com/dm/metrics" | jq .redis_status'
   
   # Check application logs
   tail -f /var/log/app.log | grep -i redis
   ```

4. **Verify Message Flow Resumes**
   - Send test message
   - Verify SSE delivery resumes
   - Check `relay_lag` returns to `false`

5. **Post-Recovery Verification**
   - Compare message count in DB vs delivered count
   - Check for any messages stuck during outage
   - Verify all SSE connections reconnected

### Post-Mortem Checklist

- [ ] Document outage duration
- [ ] Count messages stored but not delivered
- [ ] Verify all messages eventually delivered
- [ ] Review Redis logs for root cause
- [ ] Update runbook if new issues discovered

## Rollback/Hotfix Procedures

### Feature Flag Rollback

**Scenario:** Need to disable E2EE DM feature immediately

**Steps:**

1. **Set Environment Variable**
   ```bash
   # In production environment
   export E2EE_DM_ENABLED=false
   
   # Or update .env file
   echo "E2EE_DM_ENABLED=false" >> .env
   ```

2. **Restart Application**
   ```bash
   # Restart FastAPI application
   sudo systemctl restart triviapay-api
   # Or
   docker-compose restart api
   ```

3. **Verify Rollback**
   ```bash
   # Check that endpoints return 403
   curl -X GET "https://api.example.com/dm/conversations" \
     -H "Authorization: Bearer USER_TOKEN"
   # Expected: {"detail": "E2EE DM is not enabled"}
   ```

4. **Monitor**
   - Check application logs for errors
   - Verify existing SSE connections closed gracefully
   - Confirm no new E2EE DM requests processed

### Database Migration Rollback

**Scenario:** Need to rollback Alembic migration

**Steps:**

1. **Identify Migration to Rollback**
   ```bash
   alembic current
   alembic history
   ```

2. **Rollback Migration**
   ```bash
   # Rollback one step
   alembic downgrade -1
   
   # Or rollback to specific revision
   alembic downgrade <revision_id>
   ```

3. **Verify Rollback**
   ```bash
   # Check current revision
   alembic current
   
   # Verify tables dropped (if applicable)
   psql -d triviapay -c "\dt e2ee_*"
   ```

4. **Restart Application**
   ```bash
   sudo systemctl restart triviapay-api
   ```

**⚠️ WARNING:** Never drop E2EE tables during live rollback if users have data. Use feature flag instead.

### Code Rollback (Git)

**Scenario:** Need to rollback to previous code version

**Steps:**

1. **Identify Commit to Rollback To**
   ```bash
   git log --oneline -10
   ```

2. **Create Rollback Branch**
   ```bash
   git checkout -b rollback-e2ee-$(date +%Y%m%d)
   git reset --hard <previous_commit_hash>
   ```

3. **Deploy Rollback**
   ```bash
   # Push to production branch
   git push origin rollback-e2ee-$(date +%Y%m%d) --force
   
   # Trigger deployment
   # (Follow your deployment process)
   ```

4. **Verify Rollback**
   - Test critical endpoints
   - Check application logs
   - Monitor error rates

## Stuck Device Remediation

### Detection

**Symptoms:**
- User cannot receive messages
- OTPK pool exhausted (< 5 prekeys)
- Device hasn't uploaded new prekeys in weeks
- Metrics show device with `prekeys_remaining: 0`

**Verification:**
```bash
# Check device status
curl -X GET "https://api.example.com/e2ee/devices" \
  -H "Authorization: Bearer USER_TOKEN" | jq '.devices[] | select(.prekeys_remaining < 5)'
```

### Remediation Steps

1. **Identify Affected Devices**
   ```sql
   -- Query devices with low OTPK pools
   SELECT d.device_id, d.user_id, d.device_name, kb.prekeys_remaining
   FROM e2ee_devices d
   JOIN e2ee_key_bundles kb ON d.device_id = kb.device_id
   WHERE kb.prekeys_remaining < 5
   AND d.status = 'active';
   ```

2. **Send Push Notification** (if available)
   ```bash
   # Use your push notification service
   # Notify user: "Please open the app to refresh your security keys"
   ```

3. **Send Email Reminder** (if user has email)
   ```bash
   # Use your email service
   # Template: "Your device needs to refresh security keys. Please open the app."
   ```

4. **Manual Intervention** (if automated fails)
   - Contact user directly
   - Guide them to open app
   - Verify device uploads new prekeys

5. **Monitor Recovery**
   ```bash
   # Watch for device to upload new prekeys
   watch -n 30 'curl -s "https://api.example.com/dm/metrics" | jq .otpk_pools'
   ```

### Prevention

- Set up automated alerts for devices with < 5 prekeys
- Send proactive reminders at 10 prekeys remaining
- Monitor device last_seen_at and alert on inactivity

## Abuse Burst Response

### Detection

**Symptoms:**
- Sudden spike in message rate
- High rate limit violations (429 errors)
- Unusual fan-out patterns (one user messaging many)
- High conversation churn

**Verification:**
```bash
# Check metrics
curl -X GET "https://api.example.com/dm/metrics" \
  -H "Authorization: Bearer ADMIN_TOKEN" | jq '.message_stats'

# Check rate limit violations
grep "429" /var/log/app.log | tail -50
```

### Immediate Actions

1. **Identify Abusive User**
   ```sql
   -- Find users with high message rate
   SELECT sender_user_id, COUNT(*) as msg_count
   FROM dm_messages
   WHERE created_at > NOW() - INTERVAL '1 hour'
   GROUP BY sender_user_id
   ORDER BY msg_count DESC
   LIMIT 10;
   ```

2. **Check User's Rate Limit Status**
   ```bash
   # Review user's recent activity
   # Check if legitimate (e.g., group admin) or abuse
   ```

3. **Apply Throttling** (if automated)
   - System should auto-throttle at rate limits
   - Verify 429 responses include `retry_in_seconds`

4. **Manual Block** (if severe)
   ```bash
   # Block user via admin interface or API
   # This prevents all messaging from that user
   ```

### Escalation

1. **Review Metadata Patterns**
   - Check conversation churn
   - Review fan-out patterns
   - Analyze timing patterns

2. **Content Review** (if policy allows)
   - ⚠️ **NEVER** decrypt or view message content
   - Review metadata only: frequency, recipients, timing

3. **Account Action**
   - Temporary suspension
   - Permanent ban
   - Legal action (if applicable)

### Post-Incident

- [ ] Document abuse pattern
- [ ] Update rate limits if needed
- [ ] Review detection thresholds
- [ ] Update runbook with lessons learned

## Monitoring and Alerting Setup

### Key Metrics to Monitor

1. **Redis Status**
   - Connection status
   - Pub/sub lag
   - Error rate

2. **OTPK Pools**
   - Devices with < 5 prekeys
   - Average prekeys per device
   - Prekey claim rate

3. **Message Delivery**
   - Messages sent per minute
   - Delivery latency (p95, p99)
   - SSE connection count
   - Disconnect rate

4. **Error Rates**
   - 409 errors (PREKEYS_EXHAUSTED, BUNDLE_STALE, DEVICE_REVOKED)
   - 429 errors (rate limits)
   - 403 errors (blocks)
   - 500 errors (server errors)

5. **Identity Changes**
   - Identity key changes per day per user
   - Devices with identity changes > 2/day

6. **Signed Prekey Age**
   - Devices with prekeys > 90 days old
   - Average prekey age

### Alert Thresholds

**Critical Alerts:**
- Redis unavailable for > 60 seconds
- OTPK pool < 5 for any device
- Identity changes > 2/day for any user
- SSE connection failure rate > 10%

**Warning Alerts:**
- OTPK pool < 10 for any device
- Signed prekey age > 90 days
- Message delivery latency p99 > 500ms
- Redis pub/sub lag > 2 seconds

### Monitoring Tools Setup

**Prometheus/Grafana:**
```yaml
# Example alert rules
groups:
  - name: e2ee_alerts
    rules:
      - alert: RedisDown
        expr: redis_up == 0
        for: 1m
        annotations:
          summary: "Redis is down"
      
      - alert: LowOTPKPool
        expr: e2ee_otpk_remaining < 5
        for: 5m
        annotations:
          summary: "Device has low OTPK pool"
```

**Application Logging:**
- Log all 409, 429, 403 errors with context
- Log identity key changes as security events
- Log device revocations
- **Never** log ciphertext or plaintext

## Incident Response

### Severity Levels

**P0 - Critical:**
- System completely down
- Data loss risk
- Security breach

**P1 - High:**
- Major feature broken
- Significant performance degradation
- Security concern

**P2 - Medium:**
- Minor feature issues
- Performance impact on subset of users

**P3 - Low:**
- Cosmetic issues
- Minor performance impact

### Response Procedures

1. **Acknowledge Incident**
   - Post in incident channel
   - Update status page
   - Assign on-call engineer

2. **Assess Impact**
   - How many users affected?
   - What functionality broken?
   - Is data at risk?

3. **Contain**
   - Apply feature flag if needed
   - Block abusive users if applicable
   - Scale resources if needed

4. **Resolve**
   - Follow appropriate runbook
   - Deploy fix if needed
   - Verify resolution

5. **Communicate**
   - Update status page
   - Notify affected users (if applicable)
   - Post resolution in incident channel

6. **Post-Mortem**
   - Document root cause
   - Identify improvements
   - Update runbooks
   - Schedule follow-up

### On-Call Rotation

- Primary on-call: 24/7 coverage
- Escalation path: Team lead → Engineering manager
- PagerDuty/Slack integration for alerts

## Emergency Contacts

- **Engineering Lead:** [Contact Info]
- **DevOps Lead:** [Contact Info]
- **Security Team:** [Contact Info]
- **Database Admin:** [Contact Info]

## Useful Commands

### Database Queries

```sql
-- Find devices with low OTPK pools
SELECT d.device_id, u.username, kb.prekeys_remaining
FROM e2ee_devices d
JOIN users u ON d.user_id = u.account_id
JOIN e2ee_key_bundles kb ON d.device_id = kb.device_id
WHERE kb.prekeys_remaining < 5
AND d.status = 'active';

-- Find users with high message rates
SELECT sender_user_id, COUNT(*) as msg_count
FROM dm_messages
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY sender_user_id
ORDER BY msg_count DESC
LIMIT 10;

-- Check Redis lag (if stored in DB)
SELECT COUNT(*) as undelivered
FROM dm_messages m
LEFT JOIN dm_delivery d ON m.id = d.message_id
WHERE d.delivered_at IS NULL
AND m.created_at > NOW() - INTERVAL '5 minutes';
```

### API Endpoints

```bash
# Check system health
curl https://api.example.com/health

# Get metrics (admin only)
curl -X GET "https://api.example.com/dm/metrics" \
  -H "Authorization: Bearer ADMIN_TOKEN"

# Check Redis status
curl -X GET "https://api.example.com/live-chat/metrics" \
  -H "Authorization: Bearer ADMIN_TOKEN" | jq .redis_status
```

## Maintenance Windows

- **Scheduled:** [Day/Time]
- **Duration:** [Duration]
- **Notification:** [How users are notified]
- **Rollback Plan:** Feature flag disable if issues

## Change Management

- All changes go through code review
- Feature flags for gradual rollout
- Canary deployments for major changes
- Rollback plan required for all deployments

