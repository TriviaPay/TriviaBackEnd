# Redis Setup Guide

## Overview
Redis is used for real-time message delivery via Pub/Sub for E2EE DMs, Groups, and Status features. The application gracefully handles Redis being unavailable (falls back to polling mode).

## Environment Variables

### Required
- **`REDIS_URL`** - Redis connection URL
  - Default: `redis://localhost:6379/0` (for local development)
  - Format: `redis://[username:password@]host:port[/database]`

### Optional
- **`REDIS_RETRY_INTERVAL_SECONDS`** - How long to wait before retrying connection (default: 60)
- **`REDIS_PUBSUB_LAG_ALERT_THRESHOLD_MS`** - Alert threshold for Redis lag (default: 2000ms)

## Local Development Setup

### macOS (using Homebrew)
```bash
# Install Redis
brew install redis

# Start Redis (runs in background)
brew services start redis

# Or run manually (foreground)
redis-server

# Test connection
redis-cli ping
# Should return: PONG
```

### Configuration
Add to your `.env` file (optional, defaults to localhost):
```
REDIS_URL=redis://localhost:6379/0
```

## Cloud/Production Setup

### Option 1: Upstash (Recommended for Serverless/Vercel)
**Best for:** Serverless deployments, global edge locations, free tier

1. Sign up at https://upstash.com
2. Create a new Redis database
3. Choose your region (closest to your NeonDB region)
4. Copy the Redis URL from the dashboard
5. Add to your `.env` or Vercel environment variables:
   ```
   REDIS_URL=redis://default:your-password@your-region.upstash.io:6379
   ```

**Free Tier:**
- 10,000 commands/day
- 256MB storage
- Global edge locations

### Option 2: Redis Cloud
**Best for:** Traditional deployments, larger scale

1. Sign up at https://redis.com/try-free/
2. Create a database
3. Copy the connection URL
4. Add to your environment:
   ```
   REDIS_URL=redis://default:password@redis-12345.c1.us-east-1-1.ec2.cloud.redislabs.com:12345
   ```

**Free Tier:**
- 30MB storage
- No command limits

### Option 3: Railway / Render
If you're deploying on Railway or Render:
1. Add Redis add-on from their marketplace
2. They'll provide the `REDIS_URL` automatically
3. Use the provided connection string

## Testing Redis Connection

### From Python
```python
from utils.redis_pubsub import get_redis

redis = get_redis()
if redis:
    print("Redis connected!")
else:
    print("Redis unavailable")
```

### From Terminal
```bash
# Test local Redis
redis-cli ping

# Test remote Redis (if you have redis-cli installed)
redis-cli -u "redis://default:password@host:port" ping
```

## Redis URL Formats

### Local (no password)
```
redis://localhost:6379/0
```

### With Password
```
redis://:password@localhost:6379/0
```

### With Username and Password
```
redis://username:password@host:6379/0
```

### TLS/SSL (for cloud providers)
```
rediss://default:password@host:port
```
Note: Some providers use `rediss://` (with double 's') for SSL connections.

## Troubleshooting

### Redis Connection Errors
If you see `Error 111 connecting to localhost:6379`:
- **Local:** Make sure Redis is running (`brew services start redis` or `redis-server`)
- **Cloud:** Check your `REDIS_URL` is correct and includes password
- **Network:** Check firewall/security group allows Redis port (6379)

### Application Behavior When Redis is Unavailable
- ✅ REST endpoints still work (messages stored in DB)
- ✅ SSE endpoint works but only sends heartbeats
- ✅ Clients should detect `relay_lag: true` in heartbeats and poll REST endpoints
- ⚠️ Real-time updates won't work until Redis is available

### Checking Redis Status
```bash
# Check if Redis is running
redis-cli ping

# View Redis info
redis-cli info

# Monitor Redis commands
redis-cli monitor
```

## Production Checklist

- [ ] Set `REDIS_URL` in production environment variables
- [ ] Use a managed Redis service (Upstash, Redis Cloud, etc.)
- [ ] Enable Redis persistence if needed (for message queuing)
- [ ] Set up Redis monitoring/alerts
- [ ] Configure Redis password/authentication
- [ ] Use TLS/SSL for Redis connections in production
- [ ] Set appropriate memory limits
- [ ] Configure Redis eviction policy if needed

## Cost Estimates

### Upstash
- Free: 10K commands/day (good for development/small apps)
- Pay-as-you-go: ~$0.20 per 100K commands

### Redis Cloud
- Free: 30MB (good for development)
- Paid: Starts at ~$10/month for 100MB

### AWS ElastiCache
- Starts at ~$15/month for t3.micro instance

## Recommended Setup

**For Development:**
- Use local Redis (`redis://localhost:6379/0`)

**For Production (with NeonDB):**
- Use **Upstash** (serverless-friendly, works well with Vercel/serverless)
- Or **Redis Cloud** (if you need more storage/features)

Both integrate easily with NeonDB and work well for E2EE messaging use cases.

