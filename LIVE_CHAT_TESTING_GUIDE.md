# Live Chat Feature - Testing Guide

## Overview

This document provides comprehensive testing instructions for the Live Chat feature implementation. The live chat system allows users to communicate in real-time during draw windows (1 hour before and after each draw).

## Feature Components

### 1. Database Models
- `LiveChatSession`: Manages chat sessions
- `LiveChatMessage`: Stores individual messages
- `LiveChatLike`: Tracks likes on messages and sessions
- `LiveChatViewer`: Tracks active viewers

### 2. REST API Endpoints
- `GET /live-chat/status`: Get chat status and session info
- `GET /live-chat/messages`: Get recent messages
- `POST /live-chat/like`: Like the current session
- `POST /live-chat/like-message/{message_id}`: Like a specific message

### 3. WebSocket Endpoint
- `WS /live-chat/ws/{session_id}`: Real-time chat communication

## Environment Configuration

Add these environment variables to your `.env` file:

```env
# Live Chat Configuration
LIVE_CHAT_ENABLED=true
LIVE_CHAT_PRE_DRAW_HOURS=1
LIVE_CHAT_POST_DRAW_HOURS=1
LIVE_CHAT_MAX_MESSAGES_PER_USER_PER_MINUTE=10
LIVE_CHAT_MESSAGE_HISTORY_LIMIT=100
```

## Database Setup

### 1. Run Migration

```bash
# Apply the migration to create live chat tables
alembic upgrade head
```

### 2. Verify Tables Created

```sql
-- Check if tables exist
SELECT table_name FROM information_schema.tables 
WHERE table_name LIKE 'live_chat_%';

-- Expected tables:
-- live_chat_sessions
-- live_chat_messages
-- live_chat_likes
-- live_chat_viewers
```

## Testing Methods

### 1. Unit Tests

Run the comprehensive test suite:

```bash
# Run all live chat tests
pytest tests/test_live_chat.py -v

# Run specific test classes
pytest tests/test_live_chat.py::TestLiveChatModels -v
pytest tests/test_live_chat.py::TestLiveChatEndpoints -v
pytest tests/test_live_chat.py::TestLiveChatWebSocket -v
```

### 2. Manual API Testing

#### Test Chat Status

```bash
# Test when chat is disabled
curl -X GET "http://localhost:8000/live-chat/status" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Expected response when disabled:
{
  "enabled": false,
  "message": "Live chat is disabled"
}
```

#### Test Getting Messages

```bash
# Get recent messages
curl -X GET "http://localhost:8000/live-chat/messages?limit=50" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Expected response:
{
  "messages": [
    {
      "id": 1,
      "user_id": 1234567890,
      "username": "testuser",
      "profile_pic": "https://example.com/pic.jpg",
      "message": "Hello everyone!",
      "message_type": "text",
      "likes": 0,
      "created_at": "2024-01-01T12:00:00",
      "is_winner": false,
      "is_host": false
    }
  ]
}
```

#### Test Liking Session

```bash
# Like the current session
curl -X POST "http://localhost:8000/live-chat/like" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json"

# Expected response:
{
  "message": "Session liked successfully",
  "total_likes": 1
}
```

#### Test Liking Message

```bash
# Like a specific message
curl -X POST "http://localhost:8000/live-chat/like-message/1" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json"

# Expected response:
{
  "message": "Message liked successfully",
  "likes": 1
}
```

### 3. WebSocket Testing

#### Using wscat (Install: `npm install -g wscat`)

```bash
# Connect to WebSocket
wscat -c "ws://localhost:8000/live-chat/ws/1?token=YOUR_JWT_TOKEN"

# Send a message
{"type": "message", "message": "Hello from WebSocket!"}

# Send ping
{"type": "ping"}
```

#### Using Python WebSocket Client

```python
import asyncio
import websockets
import json

async def test_websocket():
    uri = "ws://localhost:8000/live-chat/ws/1?token=YOUR_JWT_TOKEN"
    
    async with websockets.connect(uri) as websocket:
        # Send a message
        message = {
            "type": "message",
            "message": "Hello from Python!"
        }
        await websocket.send(json.dumps(message))
        
        # Listen for responses
        response = await websocket.recv()
        print(f"Received: {response}")
        
        # Send ping
        ping = {"type": "ping"}
        await websocket.send(json.dumps(ping))
        
        pong = await websocket.recv()
        print(f"Pong: {pong}")

# Run the test
asyncio.run(test_websocket())
```

### 4. Frontend Integration Testing

#### JavaScript WebSocket Client

```javascript
// Connect to WebSocket
const ws = new WebSocket('ws://localhost:8000/live-chat/ws/1?token=YOUR_JWT_TOKEN');

ws.onopen = function(event) {
    console.log('Connected to live chat');
    
    // Send a message
    ws.send(JSON.stringify({
        type: 'message',
        message: 'Hello from frontend!'
    }));
};

ws.onmessage = function(event) {
    const data = JSON.parse(event.data);
    console.log('Received:', data);
    
    if (data.type === 'new_message') {
        // Display new message in chat
        displayMessage(data.message);
    } else if (data.type === 'session_like_update') {
        // Update like count
        updateLikeCount(data.total_likes);
    }
};

ws.onclose = function(event) {
    console.log('Disconnected from live chat');
};

ws.onerror = function(error) {
    console.error('WebSocket error:', error);
};
```

## Testing Scenarios

### 1. Chat Window Timing

Test that chat is only active during the configured window:

```bash
# Set draw time to 30 minutes from now
export DRAW_TIME_HOUR=$(date -d '+30 minutes' +%H)
export DRAW_TIME_MINUTE=$(date -d '+30 minutes' +%M)

# Chat should be active (within 1 hour before draw)
curl -X GET "http://localhost:8000/live-chat/status" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Set draw time to 2 hours from now
export DRAW_TIME_HOUR=$(date -d '+2 hours' +%H)
export DRAW_TIME_MINUTE=$(date -d '+2 hours' +%M)

# Chat should be inactive (more than 1 hour before draw)
curl -X GET "http://localhost:8000/live-chat/status" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

### 2. Rate Limiting

Test message rate limiting:

```python
import asyncio
import websockets
import json

async def test_rate_limiting():
    uri = "ws://localhost:8000/live-chat/ws/1?token=YOUR_JWT_TOKEN"
    
    async with websockets.connect(uri) as websocket:
        # Send messages rapidly
        for i in range(15):  # More than the limit of 10
            message = {
                "type": "message",
                "message": f"Message {i}"
            }
            await websocket.send(json.dumps(message))
            
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                data = json.loads(response)
                if data.get("type") == "error":
                    print(f"Rate limited: {data['message']}")
                    break
            except asyncio.TimeoutError:
                pass

asyncio.run(test_rate_limiting())
```

### 3. Multiple Users

Test with multiple users in the same session:

```python
import asyncio
import websockets
import json

async def user_chat(user_token, user_name):
    uri = f"ws://localhost:8000/live-chat/ws/1?token={user_token}"
    
    async with websockets.connect(uri) as websocket:
        # Send a message
        message = {
            "type": "message",
            "message": f"Hello from {user_name}!"
        }
        await websocket.send(json.dumps(message))
        
        # Listen for other messages
        while True:
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                data = json.loads(response)
                print(f"{user_name} received: {data}")
            except asyncio.TimeoutError:
                break

# Test with multiple users
async def test_multiple_users():
    tokens = ["TOKEN1", "TOKEN2", "TOKEN3"]
    names = ["User1", "User2", "User3"]
    
    tasks = []
    for token, name in zip(tokens, names):
        task = asyncio.create_task(user_chat(token, name))
        tasks.append(task)
    
    await asyncio.gather(*tasks)

asyncio.run(test_multiple_users())
```

### 4. Authentication Testing

Test WebSocket authentication:

```bash
# Test with invalid token
wscat -c "ws://localhost:8000/live-chat/ws/1?token=invalid_token"

# Test with expired token
wscat -c "ws://localhost:8000/live-chat/ws/1?token=expired_jwt_token"

# Test with valid token
wscat -c "ws://localhost:8000/live-chat/ws/1?token=valid_jwt_token"
```

## Performance Testing

### 1. Load Testing with Artillery

Create `artillery-config.yml`:

```yaml
config:
  target: 'http://localhost:8000'
  phases:
    - duration: 60
      arrivalRate: 10
scenarios:
  - name: "Live Chat Load Test"
    weight: 100
    flow:
      - get:
          url: "/live-chat/status"
          headers:
            Authorization: "Bearer {{ $randomString() }}"
      - post:
          url: "/live-chat/like"
          headers:
            Authorization: "Bearer {{ $randomString() }}"
```

Run load test:

```bash
artillery run artillery-config.yml
```

### 2. WebSocket Load Testing

```python
import asyncio
import websockets
import json
import time

async def websocket_load_test(num_connections=100):
    async def single_connection(connection_id):
        uri = f"ws://localhost:8000/live-chat/ws/1?token=TOKEN_{connection_id}"
        try:
            async with websockets.connect(uri) as websocket:
                # Send a message
                message = {
                    "type": "message",
                    "message": f"Load test message from connection {connection_id}"
                }
                await websocket.send(json.dumps(message))
                
                # Keep connection alive
                await asyncio.sleep(30)
        except Exception as e:
            print(f"Connection {connection_id} failed: {e}")
    
    # Create multiple connections
    tasks = []
    for i in range(num_connections):
        task = asyncio.create_task(single_connection(i))
        tasks.append(task)
    
    start_time = time.time()
    await asyncio.gather(*tasks)
    end_time = time.time()
    
    print(f"Load test completed in {end_time - start_time} seconds")

# Run load test
asyncio.run(websocket_load_test(50))
```

## Monitoring and Debugging

### 1. Log Monitoring

Monitor application logs for live chat activity:

```bash
# Monitor logs in real-time
tail -f logs/app.log | grep -i "live_chat\|websocket"

# Check for errors
grep -i "error\|exception" logs/app.log | grep -i "live_chat"
```

### 2. Database Monitoring

Monitor database activity:

```sql
-- Check active sessions
SELECT * FROM live_chat_sessions WHERE is_active = true;

-- Check recent messages
SELECT * FROM live_chat_messages ORDER BY created_at DESC LIMIT 10;

-- Check viewer count
SELECT session_id, COUNT(*) as viewer_count 
FROM live_chat_viewers 
WHERE is_active = true 
GROUP BY session_id;

-- Check like activity
SELECT session_id, COUNT(*) as like_count 
FROM live_chat_likes 
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY session_id;
```

### 3. WebSocket Connection Monitoring

```python
# Monitor WebSocket connections
import asyncio
from routers.live_chat import manager

async def monitor_connections():
    while True:
        print(f"Active connections: {len(manager.active_connections)}")
        for session_id, connections in manager.active_connections.items():
            print(f"Session {session_id}: {len(connections)} connections")
        await asyncio.sleep(10)

# Run monitoring
asyncio.run(monitor_connections())
```

## Troubleshooting

### Common Issues

1. **Chat not active outside draw window**
   - Check `LIVE_CHAT_PRE_DRAW_HOURS` and `LIVE_CHAT_POST_DRAW_HOURS` settings
   - Verify draw time configuration

2. **WebSocket connection fails**
   - Check JWT token validity
   - Verify WebSocket endpoint URL
   - Check CORS settings

3. **Messages not appearing**
   - Check database connection
   - Verify user authentication
   - Check rate limiting settings

4. **Likes not working**
   - Verify user hasn't already liked
   - Check session/message existence
   - Verify database constraints

### Debug Commands

```bash
# Check environment variables
env | grep LIVE_CHAT

# Check database tables
psql -d your_database -c "\dt live_chat_*"

# Test API endpoints
curl -v -X GET "http://localhost:8000/live-chat/status" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Check WebSocket endpoint
wscat -c "ws://localhost:8000/live-chat/ws/1?token=YOUR_JWT_TOKEN"
```

## Security Considerations

1. **Authentication**: All endpoints require valid JWT tokens
2. **Rate Limiting**: Prevents spam and abuse
3. **Input Validation**: Messages are validated before storage
4. **CORS**: Properly configured for frontend integration
5. **SQL Injection**: Using SQLAlchemy ORM prevents injection attacks

## Production Deployment

### 1. Environment Variables

Set production environment variables:

```env
LIVE_CHAT_ENABLED=true
LIVE_CHAT_PRE_DRAW_HOURS=1
LIVE_CHAT_POST_DRAW_HOURS=1
LIVE_CHAT_MAX_MESSAGES_PER_USER_PER_MINUTE=10
LIVE_CHAT_MESSAGE_HISTORY_LIMIT=100
```

### 2. Database Migration

```bash
# Run migration in production
alembic upgrade head
```

### 3. Monitoring Setup

- Set up log monitoring for live chat activity
- Monitor WebSocket connection counts
- Track message and like rates
- Monitor database performance

### 4. Load Balancing

For WebSocket connections, ensure your load balancer supports sticky sessions or use a WebSocket-compatible load balancer.

## Conclusion

This testing guide provides comprehensive coverage of the live chat feature. Follow these steps to ensure proper functionality and performance in both development and production environments.
