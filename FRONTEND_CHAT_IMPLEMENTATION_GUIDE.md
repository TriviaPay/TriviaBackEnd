# Frontend Chat Implementation Guide

## Overview

This guide covers how to implement the three chat types (Global Chat, Private Chat, Trivia Live Chat) in your frontend using Pusher for real-time messaging and OneSignal for push notifications.

## Table of Contents

1. [Setup & Configuration](#setup--configuration)
2. [Global Chat Implementation](#global-chat-implementation)
3. [Private Chat Implementation](#private-chat-implementation)
4. [Trivia Live Chat Implementation](#trivia-live-chat-implementation)
5. [OneSignal Push Notifications](#onesignal-push-notifications)
6. [UI/UX Requirements](#uiux-requirements)

---

## Setup & Configuration

### 1. Install Dependencies

```bash
npm install pusher-js
# or
yarn add pusher-js

# For OneSignal (React Native example)
npm install react-native-onesignal
# or for web
npm install onesignal-cordova-plugin
```

### 2. Environment Variables

```env
PUSHER_APP_KEY=your_pusher_key
PUSHER_CLUSTER=us2
PUSHER_AUTH_ENDPOINT=https://your-api.com/pusher/auth

ONESIGNAL_APP_ID=your_onesignal_app_id
API_BASE_URL=https://your-api.com
```

### 3. Initialize Pusher Client

```javascript
import Pusher from 'pusher-js';

// Initialize Pusher
const pusher = new Pusher(process.env.PUSHER_APP_KEY, {
  cluster: process.env.PUSHER_CLUSTER,
  authEndpoint: `${process.env.API_BASE_URL}/pusher/auth`,
  auth: {
    headers: {
      Authorization: `Bearer ${userToken}` // Your JWT token
    }
  }
});
```

---

## Global Chat Implementation

### Endpoints

#### 1. Send Message
```
POST /global-chat/send
Authorization: Bearer <token>
Content-Type: application/json

{
  "message": "Hello everyone!",
  "client_message_id": "msg_1234567890" // Optional, for idempotency
}

Response:
{
  "message_id": 123,
  "created_at": "2024-12-20T10:30:00Z",
  "duplicate": false
}
```

#### 2. Get Messages
```
GET /global-chat/messages?limit=50&before=123
Authorization: Bearer <token>

Response:
{
  "messages": [
    {
      "id": 123,
      "user_id": 1142961859,
      "username": "john_doe",
      "profile_pic": "https://...",
      "message": "Hello everyone!",
      "created_at": "2024-12-20T10:30:00Z",
      "is_from_trivia_live": false
    }
  ]
}
```

### Frontend Implementation

```javascript
// GlobalChat.jsx
import { useEffect, useState, useRef } from 'react';
import Pusher from 'pusher-js';

function GlobalChat({ userToken, currentUserId }) {
  const [messages, setMessages] = useState([]);
  const [inputMessage, setInputMessage] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const pusherChannelRef = useRef(null);
  const clientMessageIdCounter = useRef(0);

  // Initialize Pusher subscription
  useEffect(() => {
    const pusher = new Pusher(process.env.PUSHER_APP_KEY, {
      cluster: process.env.PUSHER_CLUSTER,
      authEndpoint: `${process.env.API_BASE_URL}/pusher/auth`,
      auth: {
        headers: {
          Authorization: `Bearer ${userToken}`
        }
      }
    });

    // Subscribe to global chat channel
    const channel = pusher.subscribe('global-chat');
    pusherChannelRef.current = channel;

    // Listen for new messages
    channel.bind('new-message', (data) => {
      setMessages(prev => {
        // Avoid duplicates
        if (prev.some(m => m.id === data.id)) {
          return prev;
        }
        return [...prev, {
          ...data,
          is_own_message: data.user_id === currentUserId
        }];
      });
    });

    // Load initial messages
    loadMessages();

    return () => {
      channel.unbind_all();
      channel.unsubscribe();
      pusher.disconnect();
    };
  }, [userToken, currentUserId]);

  const loadMessages = async () => {
    try {
      const response = await fetch(
        `${process.env.API_BASE_URL}/global-chat/messages?limit=50`,
        {
          headers: {
            Authorization: `Bearer ${userToken}`
          }
        }
      );
      const data = await response.json();
      setMessages(data.messages.map(msg => ({
        ...msg,
        is_own_message: msg.user_id === currentUserId
      })));
    } catch (error) {
      console.error('Failed to load messages:', error);
    }
  };

  const sendMessage = async () => {
    if (!inputMessage.trim()) return;

    const clientMessageId = `global_${Date.now()}_${clientMessageIdCounter.current++}`;
    const messageText = inputMessage.trim();
    setInputMessage('');

    try {
      const response = await fetch(
        `${process.env.API_BASE_URL}/global-chat/send`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${userToken}`
          },
          body: JSON.stringify({
            message: messageText,
            client_message_id: clientMessageId
          })
        }
      );

      if (!response.ok) {
        if (response.status === 429) {
          alert('Rate limit exceeded. Please wait a moment.');
        } else {
          throw new Error('Failed to send message');
        }
      }
    } catch (error) {
      console.error('Failed to send message:', error);
      // Restore message on error
      setInputMessage(messageText);
    }
  };

  return (
    <div className="global-chat">
      <div className="messages-container">
        {messages.map(msg => (
          <div
            key={msg.id}
            className={`message ${msg.is_own_message ? 'message-right' : 'message-left'}`}
          >
            {!msg.is_own_message && (
              <img src={msg.profile_pic} alt={msg.username} className="avatar" />
            )}
            <div className="message-content">
              {!msg.is_own_message && (
                <div className="username">{msg.username}</div>
              )}
              <div className="message-text">{msg.message}</div>
              <div className="timestamp">
                {new Date(msg.created_at).toLocaleTimeString()}
              </div>
            </div>
          </div>
        ))}
      </div>
      <div className="input-container">
        <input
          type="text"
          value={inputMessage}
          onChange={(e) => setInputMessage(e.target.value)}
          onKeyPress={(e) => e.key === 'Enter' && sendMessage()}
          placeholder="Type a message..."
          maxLength={1000}
        />
        <button onClick={sendMessage}>Send</button>
      </div>
    </div>
  );
}
```

### CSS for Message Alignment

```css
.global-chat {
  display: flex;
  flex-direction: column;
  height: 100vh;
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.message {
  display: flex;
  margin-bottom: 12px;
  max-width: 70%;
}

.message-left {
  align-self: flex-start;
  flex-direction: row;
}

.message-right {
  align-self: flex-end;
  flex-direction: row-reverse;
  background-color: #007bff;
  color: white;
  border-radius: 12px;
  padding: 8px 12px;
}

.message-content {
  margin: 0 8px;
}

.message-right .message-content {
  margin: 0;
}

.username {
  font-size: 12px;
  font-weight: bold;
  margin-bottom: 4px;
}

.message-text {
  word-wrap: break-word;
}

.timestamp {
  font-size: 10px;
  opacity: 0.7;
  margin-top: 4px;
}
```

---

## Private Chat Implementation

### Endpoints

#### 1. Send Private Message
```
POST /private-chat/send
Authorization: Bearer <token>
Content-Type: application/json

{
  "recipient_id": 9876543210,
  "message": "Hey, how are you?",
  "client_message_id": "private_msg_1234567890" // Optional
}

Response:
{
  "conversation_id": 456,
  "message_id": 789,
  "status": "pending", // or "accepted"
  "created_at": "2024-12-20T10:30:00Z",
  "duplicate": false
}
```

#### 2. Accept/Reject Chat Request
```
POST /private-chat/accept-reject
Authorization: Bearer <token>
Content-Type: application/json

{
  "conversation_id": 456,
  "action": "accept" // or "reject"
}

Response:
{
  "conversation_id": 456,
  "status": "accepted"
}
```

#### 3. List Conversations
```
GET /private-chat/conversations
Authorization: Bearer <token>

Response:
{
  "conversations": [
    {
      "conversation_id": 456,
      "peer_user_id": 9876543210,
      "peer_username": "jane_doe",
      "peer_profile_pic": "https://...",
      "last_message_at": "2024-12-20T10:30:00Z",
      "unread_count": 3
    }
  ]
}
```

#### 4. Get Conversation Messages
```
GET /private-chat/conversations/{conversation_id}/messages?limit=50
Authorization: Bearer <token>

Response:
{
  "messages": [
    {
      "id": 789,
      "sender_id": 9876543210,
      "sender_username": "jane_doe",
      "message": "Hey!",
      "status": "seen", // "sent", "delivered", "seen"
      "created_at": "2024-12-20T10:30:00Z",
      "delivered_at": "2024-12-20T10:30:05Z",
      "is_read": true
    }
  ]
}
```

#### 5. Mark Conversation as Read
```
POST /private-chat/conversations/{conversation_id}/mark-read?message_id=789
Authorization: Bearer <token>

Response:
{
  "conversation_id": 456,
  "last_read_message_id": 789
}
```

#### 6. Get Conversation Details
```
GET /private-chat/conversations/{conversation_id}
Authorization: Bearer <token>

Response:
{
  "conversation_id": 456,
  "peer_user_id": 9876543210,
  "peer_username": "jane_doe",
  "peer_profile_pic": "https://...",
  "status": "accepted",
  "created_at": "2024-12-20T10:00:00Z",
  "last_message_at": "2024-12-20T10:30:00Z"
}
```

### Frontend Implementation

```javascript
// PrivateChat.jsx
import { useEffect, useState, useRef } from 'react';
import Pusher from 'pusher-js';

function PrivateChat({ userToken, currentUserId, conversationId, peerUserId }) {
  const [messages, setMessages] = useState([]);
  const [conversation, setConversation] = useState(null);
  const [inputMessage, setInputMessage] = useState('');
  const [showAcceptReject, setShowAcceptReject] = useState(false);
  const pusherChannelRef = useRef(null);
  const clientMessageIdCounter = useRef(0);

  useEffect(() => {
    if (!conversationId) return;

    const pusher = new Pusher(process.env.PUSHER_APP_KEY, {
      cluster: process.env.PUSHER_CLUSTER,
      authEndpoint: `${process.env.API_BASE_URL}/pusher/auth`,
      auth: {
        headers: {
          Authorization: `Bearer ${userToken}`
        }
      }
    });

    // Subscribe to private conversation channel
    const channelName = `private-conversation-${conversationId}`;
    const channel = pusher.subscribe(channelName);
    pusherChannelRef.current = channel;

    // Listen for new messages
    channel.bind('new-message', (data) => {
      setMessages(prev => {
        if (prev.some(m => m.id === data.message_id)) {
          return prev;
        }
        return [...prev, {
          id: data.message_id,
          sender_id: data.sender_id,
          sender_username: data.sender_username,
          message: data.message,
          created_at: data.created_at,
          is_own_message: data.sender_id === currentUserId,
          status: 'sent'
        }];
      });
    });

    // Listen for conversation updates (accept/reject)
    channel.bind('conversation-updated', (data) => {
      setConversation(prev => ({
        ...prev,
        status: data.status
      }));
      if (data.status === 'accepted') {
        setShowAcceptReject(false);
      }
    });

    // Listen for read receipts
    channel.bind('messages-read', (data) => {
      if (data.reader_id !== currentUserId) {
        // Other user read messages
        setMessages(prev => prev.map(msg => {
          if (msg.sender_id === currentUserId && msg.id <= data.last_read_message_id) {
            return { ...msg, is_read: true, status: 'seen' };
          }
          return msg;
        }));
      }
    });

    loadConversation();
    loadMessages();

    return () => {
      channel.unbind_all();
      channel.unsubscribe();
      pusher.disconnect();
    };
  }, [conversationId, userToken, currentUserId]);

  const loadConversation = async () => {
    try {
      const response = await fetch(
        `${process.env.API_BASE_URL}/private-chat/conversations/${conversationId}`,
        {
          headers: {
            Authorization: `Bearer ${userToken}`
          }
        }
      );
      const data = await response.json();
      setConversation(data);
      
      if (data.status === 'pending' && data.requested_by !== currentUserId) {
        setShowAcceptReject(true);
      }
    } catch (error) {
      console.error('Failed to load conversation:', error);
    }
  };

  const loadMessages = async () => {
    try {
      const response = await fetch(
        `${process.env.API_BASE_URL}/private-chat/conversations/${conversationId}/messages?limit=50`,
        {
          headers: {
            Authorization: `Bearer ${userToken}`
          }
        }
      );
      const data = await response.json();
      setMessages(data.messages.map(msg => ({
        ...msg,
        is_own_message: msg.sender_id === currentUserId
      })));
    } catch (error) {
      console.error('Failed to load messages:', error);
    }
  };

  const sendMessage = async () => {
    if (!inputMessage.trim()) return;
    if (conversation?.status === 'rejected') {
      alert('User is not accepting private messages.');
      return;
    }

    const clientMessageId = `private_${Date.now()}_${clientMessageIdCounter.current++}`;
    const messageText = inputMessage.trim();
    setInputMessage('');

    try {
      const response = await fetch(
        `${process.env.API_BASE_URL}/private-chat/send`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${userToken}`
          },
          body: JSON.stringify({
            recipient_id: peerUserId,
            message: messageText,
            client_message_id: clientMessageId
          })
        }
      );

      const data = await response.json();
      
      if (response.status === 403 && data.detail?.includes('not accepting')) {
        alert('User is not accepting private messages.');
        setInputMessage(messageText);
        return;
      }

      if (!response.ok) {
        throw new Error('Failed to send message');
      }

      // If new conversation created, update conversationId
      if (data.conversation_id && data.conversation_id !== conversationId) {
        // Reload to get new conversation
        window.location.reload();
      }

      // Mark as read when sending
      markAsRead();
    } catch (error) {
      console.error('Failed to send message:', error);
      setInputMessage(messageText);
    }
  };

  const handleAcceptReject = async (action) => {
    try {
      const response = await fetch(
        `${process.env.API_BASE_URL}/private-chat/accept-reject`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${userToken}`
          },
          body: JSON.stringify({
            conversation_id: conversationId,
            action: action
          })
        }
      );

      if (response.ok) {
        setShowAcceptReject(false);
        loadConversation();
      }
    } catch (error) {
      console.error('Failed to accept/reject:', error);
    }
  };

  const markAsRead = async () => {
    if (messages.length === 0) return;
    
    const lastMessage = messages[messages.length - 1];
    if (lastMessage.is_own_message) return;

    try {
      await fetch(
        `${process.env.API_BASE_URL}/private-chat/conversations/${conversationId}/mark-read?message_id=${lastMessage.id}`,
        {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${userToken}`
          }
        }
      );
    } catch (error) {
      console.error('Failed to mark as read:', error);
    }
  };

  // Mark as read when viewing
  useEffect(() => {
    if (messages.length > 0) {
      markAsRead();
    }
  }, [messages.length]);

  if (showAcceptReject) {
    return (
      <div className="accept-reject-popup">
        <div className="popup-content">
          <p>{conversation?.peer_username} wants to chat with you</p>
          <div className="buttons">
            <button onClick={() => handleAcceptReject('accept')}>Accept</button>
            <button onClick={() => handleAcceptReject('reject')}>Reject</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="private-chat">
      <div className="chat-header">
        <img src={conversation?.peer_profile_pic} alt={conversation?.peer_username} />
        <span>{conversation?.peer_username}</span>
      </div>
      <div className="messages-container">
        {messages.map(msg => (
          <div
            key={msg.id}
            className={`message ${msg.is_own_message ? 'message-right' : 'message-left'}`}
          >
            {!msg.is_own_message && (
              <img src={conversation?.peer_profile_pic} alt={conversation?.peer_username} className="avatar" />
            )}
            <div className="message-content">
              {!msg.is_own_message && (
                <div className="username">{msg.sender_username}</div>
              )}
              <div className="message-text">{msg.message}</div>
              <div className="message-status">
                {msg.is_own_message && (
                  <span className={`status ${msg.status}`}>
                    {msg.status === 'seen' ? '✓✓' : msg.status === 'delivered' ? '✓✓' : '✓'}
                  </span>
                )}
                <span className="timestamp">
                  {new Date(msg.created_at).toLocaleTimeString()}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>
      <div className="input-container">
        <input
          type="text"
          value={inputMessage}
          onChange={(e) => setInputMessage(e.target.value)}
          onKeyPress={(e) => e.key === 'Enter' && sendMessage()}
          placeholder="Type a message..."
          maxLength={2000}
          disabled={conversation?.status !== 'accepted'}
        />
        <button onClick={sendMessage} disabled={conversation?.status !== 'accepted'}>
          Send
        </button>
      </div>
    </div>
  );
}

// ConversationsList.jsx - For Personal tab
function ConversationsList({ userToken, currentUserId, onSelectConversation }) {
  const [conversations, setConversations] = useState([]);
  const [unreadTotal, setUnreadTotal] = useState(0);

  useEffect(() => {
    loadConversations();
    const interval = setInterval(loadConversations, 30000); // Refresh every 30s
    return () => clearInterval(interval);
  }, []);

  const loadConversations = async () => {
    try {
      const response = await fetch(
        `${process.env.API_BASE_URL}/private-chat/conversations`,
        {
          headers: {
            Authorization: `Bearer ${userToken}`
          }
        }
      );
      const data = await response.json();
      setConversations(data.conversations);
      
      const total = data.conversations.reduce((sum, conv) => sum + conv.unread_count, 0);
      setUnreadTotal(total);
    } catch (error) {
      console.error('Failed to load conversations:', error);
    }
  };

  return (
    <div className="conversations-list">
      <div className="header">
        <h2>Personal</h2>
        {unreadTotal > 0 && (
          <span className="unread-badge">{unreadTotal}</span>
        )}
      </div>
      {conversations.map(conv => (
        <div
          key={conv.conversation_id}
          className="conversation-item"
          onClick={() => onSelectConversation(conv.conversation_id, conv.peer_user_id)}
        >
          <img src={conv.peer_profile_pic} alt={conv.peer_username} />
          <div className="conversation-info">
            <div className="peer-name">{conv.peer_username}</div>
            <div className="last-message-time">
              {new Date(conv.last_message_at).toLocaleString()}
            </div>
          </div>
          {conv.unread_count > 0 && (
            <span className="unread-count">{conv.unread_count}</span>
          )}
        </div>
      ))}
    </div>
  );
}
```

---

## Trivia Live Chat Implementation

### Endpoints

#### 1. Check Status
```
GET /trivia-live-chat/status
Authorization: Bearer <token>

Response (when active):
{
  "enabled": true,
  "is_active": true,
  "window_start": "2024-12-20T17:00:00Z",
  "window_end": "2024-12-20T23:00:00Z",
  "next_draw_time": "2024-12-20T20:00:00Z"
}

Response (when inactive):
{
  "enabled": true,
  "is_active": false,
  "message": "Trivia live chat is not currently active"
}
```

#### 2. Send Message
```
POST /trivia-live-chat/send
Authorization: Bearer <token>
Content-Type: application/json

{
  "message": "Good luck everyone!",
  "client_message_id": "trivia_msg_1234567890" // Optional
}

Response:
{
  "message_id": 123,
  "global_message_id": 456,
  "created_at": "2024-12-20T10:30:00Z",
  "duplicate": false
}
```

#### 3. Get Messages
```
GET /trivia-live-chat/messages?limit=50
Authorization: Bearer <token>

Response:
{
  "messages": [
    {
      "id": 123,
      "user_id": 1142961859,
      "username": "john_doe",
      "profile_pic": "https://...",
      "message": "Good luck!",
      "created_at": "2024-12-20T10:30:00Z"
    }
  ],
  "is_active": true,
  "window_start": "2024-12-20T17:00:00Z",
  "window_end": "2024-12-20T23:00:00Z"
}
```

### Frontend Implementation

```javascript
// TriviaLiveChat.jsx
import { useEffect, useState, useRef } from 'react';
import Pusher from 'pusher-js';

function TriviaLiveChat({ userToken, currentUserId }) {
  const [messages, setMessages] = useState([]);
  const [isActive, setIsActive] = useState(false);
  const [inputMessage, setInputMessage] = useState('');
  const pusherChannelRef = useRef(null);
  const statusCheckIntervalRef = useRef(null);

  useEffect(() => {
    checkStatus();
    
    // Check status every minute
    statusCheckIntervalRef.current = setInterval(checkStatus, 60000);

    return () => {
      if (statusCheckIntervalRef.current) {
        clearInterval(statusCheckIntervalRef.current);
      }
      if (pusherChannelRef.current) {
        pusherChannelRef.current.unbind_all();
        pusherChannelRef.current.unsubscribe();
      }
    };
  }, [userToken]);

  const checkStatus = async () => {
    try {
      const response = await fetch(
        `${process.env.API_BASE_URL}/trivia-live-chat/status`,
        {
          headers: {
            Authorization: `Bearer ${userToken}`
          }
        }
      );
      const data = await response.json();
      
      setIsActive(data.is_active);
      
      if (data.is_active && !pusherChannelRef.current) {
        subscribeToChat();
        loadMessages();
      } else if (!data.is_active && pusherChannelRef.current) {
        unsubscribeFromChat();
        setMessages([]);
      }
    } catch (error) {
      console.error('Failed to check status:', error);
    }
  };

  const subscribeToChat = () => {
    const pusher = new Pusher(process.env.PUSHER_APP_KEY, {
      cluster: process.env.PUSHER_CLUSTER,
      authEndpoint: `${process.env.API_BASE_URL}/pusher/auth`,
      auth: {
        headers: {
          Authorization: `Bearer ${userToken}`
        }
      }
    });

    const channel = pusher.subscribe('trivia-live-chat');
    pusherChannelRef.current = channel;

    channel.bind('new-message', (data) => {
      setMessages(prev => {
        if (prev.some(m => m.id === data.id)) {
          return prev;
        }
        return [...prev, {
          ...data,
          is_own_message: data.user_id === currentUserId
        }];
      });
    });
  };

  const unsubscribeFromChat = () => {
    if (pusherChannelRef.current) {
      pusherChannelRef.current.unbind_all();
      pusherChannelRef.current.unsubscribe();
      pusherChannelRef.current = null;
    }
  };

  const loadMessages = async () => {
    try {
      const response = await fetch(
        `${process.env.API_BASE_URL}/trivia-live-chat/messages?limit=50`,
        {
          headers: {
            Authorization: `Bearer ${userToken}`
          }
        }
      );
      const data = await response.json();
      
      if (data.is_active) {
        setMessages(data.messages.map(msg => ({
          ...msg,
          is_own_message: msg.user_id === currentUserId
        })));
      }
    } catch (error) {
      console.error('Failed to load messages:', error);
    }
  };

  const sendMessage = async () => {
    if (!inputMessage.trim() || !isActive) return;

    const clientMessageId = `trivia_${Date.now()}_${Math.random()}`;
    const messageText = inputMessage.trim();
    setInputMessage('');

    try {
      const response = await fetch(
        `${process.env.API_BASE_URL}/trivia-live-chat/send`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${userToken}`
          },
          body: JSON.stringify({
            message: messageText,
            client_message_id: clientMessageId
          })
        }
      );

      if (!response.ok) {
        if (response.status === 403) {
          alert('Trivia live chat is not currently active.');
        } else if (response.status === 429) {
          alert('Rate limit exceeded. Please wait a moment.');
        } else {
          throw new Error('Failed to send message');
        }
        setInputMessage(messageText);
      }
    } catch (error) {
      console.error('Failed to send message:', error);
      setInputMessage(messageText);
    }
  };

  if (!isActive) {
    return (
      <div className="trivia-live-chat inactive">
        <p>Trivia live chat is only active 3 hours before and after the draw.</p>
        <p>Please check back later!</p>
      </div>
    );
  }

  return (
    <div className="trivia-live-chat">
      <div className="chat-header">
        <h3>Trivia Live Chat</h3>
        <span className="active-badge">Active</span>
      </div>
      <div className="messages-container">
        {messages.map(msg => (
          <div
            key={msg.id}
            className={`message ${msg.is_own_message ? 'message-right' : 'message-left'}`}
          >
            {!msg.is_own_message && (
              <img src={msg.profile_pic} alt={msg.username} className="avatar" />
            )}
            <div className="message-content">
              {!msg.is_own_message && (
                <div className="username">{msg.username}</div>
              )}
              <div className="message-text">{msg.message}</div>
              <div className="timestamp">
                {new Date(msg.created_at).toLocaleTimeString()}
              </div>
            </div>
          </div>
        ))}
      </div>
      <div className="input-container">
        <input
          type="text"
          value={inputMessage}
          onChange={(e) => setInputMessage(e.target.value)}
          onKeyPress={(e) => e.key === 'Enter' && sendMessage()}
          placeholder="Type a message..."
          maxLength={1000}
        />
        <button onClick={sendMessage}>Send</button>
      </div>
    </div>
  );
}
```

---

## OneSignal Push Notifications

### 1. Register Player ID

```javascript
// OneSignalSetup.js
import OneSignal from 'react-native-onesignal'; // or appropriate SDK

async function registerOneSignalPlayer(userToken) {
  try {
    // Get player ID from OneSignal SDK
    const playerId = await OneSignal.getDeviceState().then(state => state.userId);
    
    // Determine platform
    const platform = Platform.OS === 'ios' ? 'ios' : 
                     Platform.OS === 'android' ? 'android' : 'web';

    // Register with backend
    const response = await fetch(
      `${process.env.API_BASE_URL}/onesignal/register`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${userToken}`
        },
        body: JSON.stringify({
          player_id: playerId,
          platform: platform
        })
      }
    );

    if (response.ok) {
      console.log('OneSignal player registered');
    }
  } catch (error) {
    console.error('Failed to register OneSignal player:', error);
  }
}

// Call on app start and after login
useEffect(() => {
  if (userToken) {
    registerOneSignalPlayer(userToken);
  }
}, [userToken]);
```

### 2. Handle Push Notifications

```javascript
// Handle notification received
OneSignal.setNotificationWillShowInForegroundHandler(notificationReceivedEvent => {
  const notification = notificationReceivedEvent.getNotification();
  const data = notification.additionalData;

  if (data.type === 'chat_request') {
    // Show accept/reject popup
    showChatRequestPopup(data.conversation_id, data.sender_id);
  } else if (data.type === 'private_message') {
    // Navigate to conversation
    navigateToConversation(data.conversation_id);
  }

  notificationReceivedEvent.complete(notification);
});

// Handle notification opened
OneSignal.setNotificationOpenedHandler(result => {
  const data = result.notification.additionalData;
  
  if (data.type === 'chat_request') {
    navigateToChatRequest(data.conversation_id);
  } else if (data.type === 'private_message') {
    navigateToConversation(data.conversation_id);
  }
});
```

---

## UI/UX Requirements

### Global Chat UI

- **Message Alignment:**
  - Other users' messages: **Left side** (default)
  - Own messages: **Right side** (highlighted, different background color)

- **Message Display:**
  - Username (for others only)
  - Profile picture (for others only)
  - Message text
  - Timestamp

### Private Chat UI

- **Personal Tab:**
  - List of all conversations
  - Show unread count badge on tab and each conversation
  - Sort by last message time (most recent first)

- **Chat Request Popup:**
  - Show when receiving message from new user
  - "Accept Chat" button
  - "Reject Chat" button
  - If rejected, sender sees: "User is not accepting private messages."

- **Message Display:**
  - Other users' messages: **Left side**
  - Own messages: **Right side** (highlighted)
  - Show delivery status:
    - ✓ (sent)
    - ✓✓ (delivered)
    - ✓✓ (seen - blue)

- **Read Receipts:**
  - Update status when other user reads messages
  - Show "seen" indicator

### Trivia Live Chat UI

- **Active State:**
  - Only show when chat is active (3 hours before/after draw)
  - Display active window times
  - Hide/disable input when inactive

- **Message Display:**
  - Same as global chat (left/right alignment)
  - Messages also appear in global chat

---

## Complete Integration Example

```javascript
// ChatApp.jsx - Main chat component
import { useState } from 'react';
import GlobalChat from './GlobalChat';
import PrivateChat from './PrivateChat';
import TriviaLiveChat from './TriviaLiveChat';
import ConversationsList from './ConversationsList';

function ChatApp({ userToken, currentUserId }) {
  const [activeTab, setActiveTab] = useState('global'); // 'global', 'personal', 'trivia'
  const [selectedConversation, setSelectedConversation] = useState(null);
  const [selectedPeerId, setSelectedPeerId] = useState(null);

  const handleStartChat = (peerUserId) => {
    setSelectedPeerId(peerUserId);
    setSelectedConversation(null); // Will be created on first message
    setActiveTab('personal');
  };

  const handleSelectConversation = (conversationId, peerUserId) => {
    setSelectedConversation(conversationId);
    setSelectedPeerId(peerUserId);
  };

  return (
    <div className="chat-app">
      <div className="chat-tabs">
        <button
          className={activeTab === 'global' ? 'active' : ''}
          onClick={() => setActiveTab('global')}
        >
          Global
        </button>
        <button
          className={activeTab === 'personal' ? 'active' : ''}
          onClick={() => setActiveTab('personal')}
        >
          Personal
        </button>
        <button
          className={activeTab === 'trivia' ? 'active' : ''}
          onClick={() => setActiveTab('trivia')}
        >
          Trivia Live
        </button>
      </div>

      <div className="chat-content">
        {activeTab === 'global' && (
          <GlobalChat userToken={userToken} currentUserId={currentUserId} />
        )}
        
        {activeTab === 'personal' && (
          selectedConversation || selectedPeerId ? (
            <PrivateChat
              userToken={userToken}
              currentUserId={currentUserId}
              conversationId={selectedConversation}
              peerUserId={selectedPeerId}
            />
          ) : (
            <ConversationsList
              userToken={userToken}
              currentUserId={currentUserId}
              onSelectConversation={handleSelectConversation}
              onStartChat={handleStartChat}
            />
          )
        )}
        
        {activeTab === 'trivia' && (
          <TriviaLiveChat userToken={userToken} currentUserId={currentUserId} />
        )}
      </div>
    </div>
  );
}
```

---

## Error Handling

### Rate Limiting
```javascript
if (response.status === 429) {
  const retryAfter = response.headers.get('X-Retry-After');
  alert(`Rate limit exceeded. Please wait ${retryAfter} seconds.`);
}
```

### Blocked Users
```javascript
if (response.status === 403 && data.detail?.includes('blocked')) {
  alert('User is blocked. Cannot send message.');
}
```

### Rejected Chat
```javascript
if (response.status === 403 && data.detail?.includes('not accepting')) {
  alert('User is not accepting private messages.');
}
```

---

## Best Practices

1. **Idempotency**: Always include `client_message_id` when sending messages to prevent duplicates on network retries
2. **Connection Management**: Unsubscribe from Pusher channels when component unmounts
3. **Error Recovery**: Retry failed requests with exponential backoff
4. **Message Deduplication**: Check for duplicate message IDs before adding to state
5. **Read Receipts**: Mark messages as read when user views conversation
6. **Status Polling**: Poll trivia live chat status periodically to detect when it becomes active/inactive
7. **Push Notifications**: Register OneSignal player ID on app start and after login

---

## API Endpoint Summary

### Global Chat
- `POST /global-chat/send` - Send message
- `GET /global-chat/messages` - Get messages
- `POST /global-chat/cleanup` - Cleanup old messages (admin only)

### Private Chat
- `POST /private-chat/send` - Send message
- `POST /private-chat/accept-reject` - Accept/reject request
- `GET /private-chat/conversations` - List conversations
- `GET /private-chat/conversations/{id}/messages` - Get messages
- `POST /private-chat/conversations/{id}/mark-read` - Mark as read
- `GET /private-chat/conversations/{id}` - Get conversation details

### Trivia Live Chat
- `GET /trivia-live-chat/status` - Check if active
- `POST /trivia-live-chat/send` - Send message
- `GET /trivia-live-chat/messages` - Get messages

### OneSignal
- `POST /onesignal/register` - Register player ID
- `GET /onesignal/players` - List user's players

### Pusher Auth
- `POST /pusher/auth` - Authenticate channel subscription (called automatically by Pusher client)

---

## Pusher Channels

- `global-chat` - Public channel for global messages
- `trivia-live-chat` - Public channel for trivia live messages
- `private-conversation-{conversation_id}` - Private channel for each conversation
- `presence-global-chat` - Presence channel for online status (optional)

---

This guide provides everything your frontend team needs to implement the chat system. All endpoints are documented with request/response examples, and the code examples show the complete implementation patterns.

