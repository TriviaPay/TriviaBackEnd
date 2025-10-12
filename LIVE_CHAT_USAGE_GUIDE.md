# 🎯 Live Chat Feature - Complete Usage Guide

## 📋 **Available Endpoints**

### **REST API Endpoints (visible in Swagger UI):**

1. **`GET /live-chat/status`** - Get chat status and session info
2. **`GET /live-chat/messages`** - Get recent messages
3. **`GET /live-chat/viewers`** - Get current viewer count
4. **`GET /live-chat/likes`** - Get current like count
5. **`GET /live-chat/stats`** - Get comprehensive chat statistics
6. **`POST /live-chat/like`** - Like the current session
7. **`POST /live-chat/like-message/{message_id}`** - Like a specific message
8. **`POST /live-chat/send-message`** - Send a message via REST API

### **WebSocket Endpoint (for real-time chat):**
- **`WS /live-chat/ws/{session_id}`** - Real-time messaging (not visible in Swagger)

---

## 🚀 **How to Use Live Chat**

### **1. Check Chat Status**
```bash
curl -X GET "http://localhost:8000/live-chat/status" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

**Response:**
```json
{
  "enabled": true,
  "is_active": true,
  "session": {
    "id": 1,
    "name": "Draw Chat - January 01, 2024",
    "viewer_count": 5,
    "total_likes": 12,
    "start_time": "2024-01-01T19:00:00",
    "end_time": "2024-01-01T21:00:00"
  }
}
```

### **2. Get Viewer Count**
```bash
curl -X GET "http://localhost:8000/live-chat/viewers" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

**Response:**
```json
{
  "viewer_count": 5,
  "session_id": 1,
  "session_name": "Draw Chat - January 01, 2024"
}
```

### **3. Get Like Count**
```bash
curl -X GET "http://localhost:8000/live-chat/likes" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

**Response:**
```json
{
  "total_likes": 12,
  "session_id": 1,
  "session_name": "Draw Chat - January 01, 2024"
}
```

### **4. Get Comprehensive Stats**
```bash
curl -X GET "http://localhost:8000/live-chat/stats" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

**Response:**
```json
{
  "session_id": 1,
  "session_name": "Draw Chat - January 01, 2024",
  "viewer_count": 5,
  "total_likes": 12,
  "total_messages": 45,
  "recent_messages": 8,
  "is_active": true,
  "start_time": "2024-01-01T19:00:00",
  "end_time": "2024-01-01T21:00:00"
}
```

---

## 💬 **How to Send Messages**

### **Method 1: WebSocket (Real-time - Recommended)**

**JavaScript:**
```javascript
// Connect to live chat
const ws = new WebSocket('ws://localhost:8000/live-chat/ws/1?token=YOUR_JWT_TOKEN');

ws.onopen = function() {
    console.log('Connected to live chat');
};

// Send a message
function sendMessage(message) {
    ws.send(JSON.stringify({
        type: 'message',
        message: message
    }));
}

// Receive messages
ws.onmessage = function(event) {
    const data = JSON.parse(event.data);
    
    if (data.type === 'new_message') {
        // Display new message
        displayMessage(data.message);
    } else if (data.type === 'session_like_update') {
        // Update like count
        updateLikeCount(data.total_likes);
    }
};
```

**Command Line (wscat):**
```bash
# Install wscat: npm install -g wscat
wscat -c "ws://localhost:8000/live-chat/ws/1?token=YOUR_JWT_TOKEN"

# Send a message
{"type": "message", "message": "Hello everyone!"}
```

### **Method 2: REST API (For testing)**

```bash
curl -X POST "http://localhost:8000/live-chat/send-message" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello from REST API!"}'
```

**Response:**
```json
{
  "message": "Message sent successfully",
  "message_id": 123,
  "created_at": "2024-01-01T20:30:00"
}
```

---

## 👥 **How Viewer Count Works**

### **Automatic Tracking:**
- **Viewers are automatically tracked** when they connect to the WebSocket
- **Active viewers** are those who have been seen in the last 5 minutes
- **Viewer count updates in real-time** as users join/leave

### **Manual Tracking:**
```bash
# Get current viewer count
curl -X GET "http://localhost:8000/live-chat/viewers" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

### **Frontend Integration:**
```javascript
// Poll for viewer count updates
setInterval(async () => {
    try {
        const response = await fetch('/live-chat/viewers', {
            headers: {
                'Authorization': `Bearer ${jwtToken}`
            }
        });
        const data = await response.json();
        updateViewerCount(data.viewer_count);
    } catch (error) {
        console.error('Failed to get viewer count:', error);
    }
}, 10000); // Update every 10 seconds
```

---

## ❤️ **How Likes Work**

### **Session Likes:**
```bash
# Like the current session
curl -X POST "http://localhost:8000/live-chat/like" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

### **Message Likes:**
```bash
# Like a specific message
curl -X POST "http://localhost:8000/live-chat/like-message/123" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

### **Get Like Count:**
```bash
# Get current like count
curl -X GET "http://localhost:8000/live-chat/likes" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

### **Frontend Integration:**
```javascript
// Like session
async function likeSession() {
    try {
        const response = await fetch('/live-chat/like', {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${jwtToken}`
            }
        });
        const data = await response.json();
        updateLikeCount(data.total_likes);
    } catch (error) {
        console.error('Failed to like session:', error);
    }
}

// Like message
async function likeMessage(messageId) {
    try {
        const response = await fetch(`/live-chat/like-message/${messageId}`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${jwtToken}`
            }
        });
        const data = await response.json();
        updateMessageLikes(messageId, data.likes);
    } catch (error) {
        console.error('Failed to like message:', error);
    }
}
```

---

## 📱 **Complete Frontend Implementation**

```javascript
class LiveChat {
    constructor(jwtToken) {
        this.jwtToken = jwtToken;
        this.ws = null;
        this.sessionId = 1; // Get from status endpoint
    }
    
    async connect() {
        // Get session info first
        const status = await this.getStatus();
        if (!status.is_active) {
            throw new Error('Chat is not active');
        }
        
        // Connect to WebSocket
        this.ws = new WebSocket(`ws://localhost:8000/live-chat/ws/${this.sessionId}?token=${this.jwtToken}`);
        
        this.ws.onopen = () => {
            console.log('Connected to live chat');
            this.startPolling();
        };
        
        this.ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            this.handleMessage(data);
        };
        
        this.ws.onclose = () => {
            console.log('Disconnected from live chat');
        };
    }
    
    async getStatus() {
        const response = await fetch('/live-chat/status', {
            headers: { 'Authorization': `Bearer ${this.jwtToken}` }
        });
        return await response.json();
    }
    
    async getStats() {
        const response = await fetch('/live-chat/stats', {
            headers: { 'Authorization': `Bearer ${this.jwtToken}` }
        });
        return await response.json();
    }
    
    sendMessage(message) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'message',
                message: message
            }));
        }
    }
    
    async likeSession() {
        const response = await fetch('/live-chat/like', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${this.jwtToken}` }
        });
        return await response.json();
    }
    
    async likeMessage(messageId) {
        const response = await fetch(`/live-chat/like-message/${messageId}`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${this.jwtToken}` }
        });
        return await response.json();
    }
    
    handleMessage(data) {
        switch (data.type) {
            case 'new_message':
                this.displayMessage(data.message);
                break;
            case 'session_like_update':
                this.updateLikeCount(data.total_likes);
                break;
            case 'message_like_update':
                this.updateMessageLikes(data.message_id, data.likes);
                break;
        }
    }
    
    startPolling() {
        // Poll for stats every 10 seconds
        setInterval(async () => {
            try {
                const stats = await this.getStats();
                this.updateViewerCount(stats.viewer_count);
                this.updateLikeCount(stats.total_likes);
            } catch (error) {
                console.error('Failed to get stats:', error);
            }
        }, 10000);
    }
    
    displayMessage(message) {
        // Implement your UI logic here
        console.log('New message:', message);
    }
    
    updateViewerCount(count) {
        // Update viewer count in UI
        document.getElementById('viewer-count').textContent = count;
    }
    
    updateLikeCount(count) {
        // Update like count in UI
        document.getElementById('like-count').textContent = count;
    }
    
    updateMessageLikes(messageId, likes) {
        // Update specific message likes in UI
        const messageElement = document.getElementById(`message-${messageId}`);
        if (messageElement) {
            messageElement.querySelector('.likes').textContent = likes;
        }
    }
}

// Usage
const chat = new LiveChat('YOUR_JWT_TOKEN');
chat.connect();
```

---

## 🧪 **Testing Commands**

```bash
# 1. Check if chat is active
curl -X GET "http://localhost:8000/live-chat/status" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# 2. Get viewer count
curl -X GET "http://localhost:8000/live-chat/viewers" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# 3. Get like count
curl -X GET "http://localhost:8000/live-chat/likes" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# 4. Get comprehensive stats
curl -X GET "http://localhost:8000/live-chat/stats" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# 5. Send a message via REST
curl -X POST "http://localhost:8000/live-chat/send-message" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello everyone!"}'

# 6. Like the session
curl -X POST "http://localhost:8000/live-chat/like" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# 7. Test WebSocket connection
wscat -c "ws://localhost:8000/live-chat/ws/1?token=YOUR_JWT_TOKEN"
```

---

## 🎯 **Summary**

**You now have 8 REST endpoints + 1 WebSocket endpoint:**

### **REST Endpoints:**
1. `GET /live-chat/status` - Chat status
2. `GET /live-chat/messages` - Recent messages
3. `GET /live-chat/viewers` - Viewer count
4. `GET /live-chat/likes` - Like count
5. `GET /live-chat/stats` - Comprehensive stats
6. `POST /live-chat/like` - Like session
7. `POST /live-chat/like-message/{id}` - Like message
8. `POST /live-chat/send-message` - Send message

### **WebSocket Endpoint:**
- `WS /live-chat/ws/{session_id}` - Real-time chat

**The WebSocket is where the real-time chatting happens, while the REST endpoints provide status, stats, and alternative ways to interact with the chat system!** 🚀
