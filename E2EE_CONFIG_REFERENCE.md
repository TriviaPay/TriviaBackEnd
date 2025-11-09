# E2EE Configuration Reference

Complete reference for all E2EE DM environment variables, feature flags, rate limits, and alert thresholds.

## Environment Variables

### Feature Flags

#### `E2EE_DM_ENABLED`
- **Type:** Boolean
- **Default:** `false`
- **Description:** Master feature flag to enable/disable E2EE DM system
- **Values:** `true` | `false`
- **Production:** `true`
- **Usage:** When `false`, all E2EE endpoints return `403 E2EE DM is not enabled`

#### `SEALED_SENDER_ENABLED`
- **Type:** Boolean
- **Default:** `false`
- **Description:** Enable sealed sender mode (hides sender_user_id from server)
- **Values:** `true` | `false`
- **Production:** `false` (enable after thorough testing)
- **Note:** Advanced privacy feature, requires additional client implementation

### Message Limits

#### `E2EE_DM_MAX_MESSAGE_SIZE`
- **Type:** Integer (bytes)
- **Default:** `65536` (64 KB)
- **Description:** Maximum size of encrypted message ciphertext
- **Production:** `65536`
- **Note:** Includes DR header + encrypted payload

#### `E2EE_DM_MAX_MESSAGES_PER_MINUTE`
- **Type:** Integer
- **Default:** `20`
- **Description:** Rate limit for messages per user per minute
- **Production:** `20`
- **Note:** Applies across all conversations for a user

#### `E2EE_DM_MAX_MESSAGES_PER_CONVERSATION_BURST`
- **Type:** Integer
- **Default:** `5`
- **Description:** Maximum messages in 5-second window per conversation
- **Production:** `5`
- **Note:** Prevents spam bursts in single conversation

#### `E2EE_DM_BURST_WINDOW_SECONDS`
- **Type:** Integer (seconds)
- **Default:** `5`
- **Description:** Time window for burst rate limiting
- **Production:** `5`

### Key Management

#### `E2EE_DM_PREKEY_POOL_SIZE`
- **Type:** Integer
- **Default:** `100`
- **Description:** Target number of one-time prekeys per device
- **Production:** `100`
- **Note:** Client should maintain this pool size

#### `E2EE_DM_OTPK_LOW_WATERMARK`
- **Type:** Integer
- **Default:** `5`
- **Description:** Alert threshold for low OTPK pool
- **Production:** `5`
- **Note:** Alerts triggered when pool < this value

#### `E2EE_DM_OTPK_CRITICAL_WATERMARK`
- **Type:** Integer
- **Default:** `2`
- **Description:** Critical alert threshold
- **Production:** `2`
- **Note:** Urgent alerts when pool < this value

#### `E2EE_DM_SIGNED_PREKEY_ROTATION_DAYS`
- **Type:** Integer (days)
- **Default:** `60`
- **Description:** Recommended rotation period for signed prekeys
- **Production:** `60`
- **Note:** Client should rotate every 60-90 days

#### `E2EE_DM_SIGNED_PREKEY_MAX_AGE_DAYS`
- **Type:** Integer (days)
- **Default:** `90`
- **Description:** Maximum age before warning
- **Production:** `90`
- **Note:** Alerts when prekey age exceeds this

### Identity Key Monitoring

#### `E2EE_DM_IDENTITY_CHANGE_ALERT_THRESHOLD`
- **Type:** Integer
- **Default:** `2`
- **Description:** Alert if identity key changes exceed this per day
- **Production:** `2`
- **Note:** Possible account takeover indicator

#### `E2EE_DM_IDENTITY_CHANGE_BLOCK_THRESHOLD`
- **Type:** Integer
- **Default:** `5`
- **Description:** Block bundle exposure if changes exceed this per day
- **Production:** `5`
- **Note:** Severe abuse indicator

### Transport & SSE

#### `SSE_HEARTBEAT_SECONDS`
- **Type:** Integer (seconds)
- **Default:** `25`
- **Description:** SSE heartbeat interval
- **Production:** `25`
- **Note:** Keep-alive for SSE connections

#### `SSE_MAX_MISSED_HEARTBEATS`
- **Type:** Integer
- **Default:** `2`
- **Description:** Close connection after this many missed heartbeats
- **Production:** `2`
- **Note:** ~50 seconds total before disconnect

#### `E2EE_DM_MAX_CONCURRENT_STREAMS_PER_USER`
- **Type:** Integer
- **Default:** `3`
- **Description:** Maximum SSE streams per user
- **Production:** `3`
- **Note:** Prevents abuse, allows multi-tab

#### `E2EE_DM_SSE_RECONNECT_BACKOFF_MAX_SECONDS`
- **Type:** Integer (seconds)
- **Default:** `300` (5 minutes)
- **Description:** Maximum backoff time for SSE reconnection
- **Production:** `300`

### Redis Configuration

#### `REDIS_URL`
- **Type:** String (URL)
- **Default:** `redis://localhost:6379/0`
- **Description:** Redis connection URL
- **Production:** Set to production Redis instance
- **Format:** `redis://[password@]host:port/db`

#### `REDIS_RETRY_INTERVAL_SECONDS`
- **Type:** Integer (seconds)
- **Default:** `60`
- **Description:** Retry interval if Redis unavailable
- **Production:** `60`

#### `REDIS_PUBSUB_LAG_ALERT_THRESHOLD_MS`
- **Type:** Integer (milliseconds)
- **Default:** `2000` (2 seconds)
- **Description:** Alert if pub/sub lag exceeds this
- **Production:** `2000`

### Rate Limiting

#### `E2EE_DM_RATE_LIMIT_RETRY_BASE_SECONDS`
- **Type:** Integer (seconds)
- **Default:** `60`
- **Description:** Base retry time for rate limit responses
- **Production:** `60`
- **Note:** Actual retry time calculated based on limit window

### Storage & Retention

#### `E2EE_DM_MESSAGE_HISTORY_LIMIT`
- **Type:** Integer
- **Default:** `100`
- **Description:** Default limit for message history pagination
- **Production:** `100`

#### `E2EE_DM_MESSAGE_RETENTION_DAYS`
- **Type:** Integer (days)
- **Default:** `-1` (unlimited)
- **Description:** Auto-delete messages after N days (-1 = never)
- **Production:** `-1`
- **Note:** Privacy mode can override per-user

#### `E2EE_DM_ATTACHMENT_TTL_DAYS`
- **Type:** Integer (days)
- **Default:** `30`
- **Description:** TTL for encrypted attachments in object storage
- **Production:** `30`
- **Note:** Extendable if message has `retain=true`

#### `E2EE_DM_ATTACHMENT_MAX_SIZE_BYTES`
- **Type:** Integer (bytes)
- **Default:** `10485760` (10 MB)
- **Description:** Maximum attachment size
- **Production:** `10485760`

#### `E2EE_DM_ATTACHMENT_DAILY_QUOTA_BYTES`
- **Type:** Integer (bytes)
- **Default:** `104857600` (100 MB)
- **Description:** Daily attachment quota per user
- **Production:** `104857600`

### Observability

#### `E2EE_DM_METRICS_ENABLED`
- **Type:** Boolean
- **Default:** `true`
- **Description:** Enable metrics endpoint
- **Production:** `true`

#### `E2EE_DM_LOG_LEVEL`
- **Type:** String
- **Default:** `INFO`
- **Description:** Logging level for E2EE components
- **Values:** `DEBUG` | `INFO` | `WARNING` | `ERROR`
- **Production:** `INFO`
- **Note:** Never log ciphertext, even in DEBUG

#### `E2EE_DM_SECURITY_EVENT_LOGGING`
- **Type:** Boolean
- **Default:** `true`
- **Description:** Log security events (identity changes, revocations)
- **Production:** `true`

## Configuration by Environment

### Development

```env
E2EE_DM_ENABLED=true
E2EE_DM_MAX_MESSAGES_PER_MINUTE=50
E2EE_DM_PREKEY_POOL_SIZE=50
E2EE_DM_LOG_LEVEL=DEBUG
REDIS_URL=redis://localhost:6379/0
```

### Staging

```env
E2EE_DM_ENABLED=true
E2EE_DM_MAX_MESSAGES_PER_MINUTE=20
E2EE_DM_PREKEY_POOL_SIZE=100
E2EE_DM_LOG_LEVEL=INFO
REDIS_URL=redis://staging-redis:6379/0
```

### Production

```env
E2EE_DM_ENABLED=true
E2EE_DM_MAX_MESSAGE_SIZE=65536
E2EE_DM_MAX_MESSAGES_PER_MINUTE=20
E2EE_DM_MAX_MESSAGES_PER_CONVERSATION_BURST=5
E2EE_DM_BURST_WINDOW_SECONDS=5
E2EE_DM_PREKEY_POOL_SIZE=100
E2EE_DM_OTPK_LOW_WATERMARK=5
E2EE_DM_OTPK_CRITICAL_WATERMARK=2
E2EE_DM_SIGNED_PREKEY_ROTATION_DAYS=60
E2EE_DM_SIGNED_PREKEY_MAX_AGE_DAYS=90
E2EE_DM_IDENTITY_CHANGE_ALERT_THRESHOLD=2
E2EE_DM_IDENTITY_CHANGE_BLOCK_THRESHOLD=5
SSE_HEARTBEAT_SECONDS=25
SSE_MAX_MISSED_HEARTBEATS=2
E2EE_DM_MAX_CONCURRENT_STREAMS_PER_USER=3
REDIS_URL=redis://prod-redis:6379/0
REDIS_RETRY_INTERVAL_SECONDS=60
REDIS_PUBSUB_LAG_ALERT_THRESHOLD_MS=2000
E2EE_DM_RATE_LIMIT_RETRY_BASE_SECONDS=60
E2EE_DM_MESSAGE_HISTORY_LIMIT=100
E2EE_DM_MESSAGE_RETENTION_DAYS=-1
E2EE_DM_ATTACHMENT_TTL_DAYS=30
E2EE_DM_ATTACHMENT_MAX_SIZE_BYTES=10485760
E2EE_DM_ATTACHMENT_DAILY_QUOTA_BYTES=104857600
E2EE_DM_METRICS_ENABLED=true
E2EE_DM_LOG_LEVEL=INFO
E2EE_DM_SECURITY_EVENT_LOGGING=true
```

## Alert Thresholds

### Critical Alerts

| Metric | Threshold | Action |
|--------|-----------|--------|
| Redis unavailable | > 60 seconds | Page on-call, check Redis status |
| OTPK pool < critical | < 2 prekeys | Urgent alert, notify user |
| Identity changes/day | > 5 per user | Block bundle exposure, security review |
| SSE connection failure | > 10% | Check application health |

### Warning Alerts

| Metric | Threshold | Action |
|--------|-----------|--------|
| OTPK pool < low | < 5 prekeys | Alert, send user notification |
| Signed prekey age | > 90 days | Alert, suggest rotation |
| Message delivery latency p99 | > 500ms | Investigate performance |
| Redis pub/sub lag | > 2 seconds | Check Redis performance |
| Identity changes/day | > 2 per user | Log security event, monitor |

## Feature Flag Combinations

### Gradual Rollout

```env
# Phase 1: Internal testing
E2EE_DM_ENABLED=true
# Only enable for internal users via application logic

# Phase 2: Beta users
E2EE_DM_ENABLED=true
# Enable for beta user group

# Phase 3: General availability
E2EE_DM_ENABLED=true
# Enable for all users
```

### Sealed Sender (Future)

```env
E2EE_DM_ENABLED=true
SEALED_SENDER_ENABLED=true
# Requires client implementation
```

## Configuration Validation

### Startup Checks

The application should validate configuration on startup:

- [ ] `E2EE_DM_MAX_MESSAGE_SIZE` > 0
- [ ] `E2EE_DM_MAX_MESSAGES_PER_MINUTE` > 0
- [ ] `E2EE_DM_PREKEY_POOL_SIZE` >= `E2EE_DM_OTPK_LOW_WATERMARK`
- [ ] `E2EE_DM_OTPK_LOW_WATERMARK` >= `E2EE_DM_OTPK_CRITICAL_WATERMARK`
- [ ] `SSE_HEARTBEAT_SECONDS` > 0
- [ ] `REDIS_URL` is valid format

### Runtime Validation

- Rate limits enforced per configuration
- Message size validated against `E2EE_DM_MAX_MESSAGE_SIZE`
- OTPK pool monitored against watermarks
- Prekey age checked against rotation thresholds

## Configuration Changes

### Safe Changes (No Restart Required)

- None - all changes require application restart

### Changes Requiring Restart

- All environment variables require application restart
- Feature flags require restart to take effect

### Changes Requiring Migration

- None currently - all config is runtime

## Monitoring Configuration

### Metrics Exposed

- Current configuration values (read-only)
- Active feature flags
- Rate limit settings
- Alert thresholds

### Access Control

- Metrics endpoint requires admin authentication
- Configuration values visible to admins only
- No sensitive values (passwords, keys) exposed

## Troubleshooting

### Common Issues

**Issue:** E2EE DM not working
- **Check:** `E2EE_DM_ENABLED=true`
- **Verify:** Application restarted after config change

**Issue:** Rate limits too strict
- **Check:** `E2EE_DM_MAX_MESSAGES_PER_MINUTE`
- **Adjust:** Increase if legitimate use case

**Issue:** OTPK alerts firing too often
- **Check:** `E2EE_DM_OTPK_LOW_WATERMARK`
- **Adjust:** Increase threshold if too sensitive

**Issue:** Redis connection issues
- **Check:** `REDIS_URL` format and connectivity
- **Verify:** Redis server is running and accessible

## Best Practices

1. **Use feature flags** for gradual rollout
2. **Monitor metrics** after configuration changes
3. **Document changes** in changelog
4. **Test in staging** before production
5. **Set conservative defaults** for production
6. **Review alert thresholds** regularly
7. **Keep configurations in version control** (without secrets)

