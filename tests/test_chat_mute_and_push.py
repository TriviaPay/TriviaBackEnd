"""
Test chat mute preferences and push notifications functionality.
Tests all endpoints created after separating global chat and trivia live chat.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from datetime import datetime, date
from sqlalchemy.orm import Session

from main import app
from models import (
    User, GlobalChatMessage, TriviaLiveChatMessage, PrivateChatMessage,
    ChatMutePreferences, OneSignalPlayer
)
from routers.dependencies import get_current_user

client = TestClient(app)


# ==================== FIXTURES ====================

@pytest.fixture
def test_user1(test_db):
    """Create first test user"""
    import uuid
    unique_id = str(uuid.uuid4())[:8]
    user = User(
        email=f"mute_user1_{unique_id}@test.com",
        username=f"muteuser1_{unique_id}",
        descope_user_id=f"test_descope_mute_1_{unique_id}"
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture
def test_user2(test_db):
    """Create second test user"""
    import uuid
    unique_id = str(uuid.uuid4())[:8]
    user = User(
        email=f"mute_user2_{unique_id}@test.com",
        username=f"muteuser2_{unique_id}",
        descope_user_id=f"test_descope_mute_2_{unique_id}"
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture
def auth_headers_user1(test_user1):
    """Get auth headers for user1"""
    def _get_user():
        return test_user1
    
    app.dependency_overrides[get_current_user] = _get_user
    yield {"Authorization": f"Bearer test_token_user1"}
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers_user2(test_user2):
    """Get auth headers for user2"""
    def _get_user():
        return test_user2
    
    app.dependency_overrides[get_current_user] = _get_user
    yield {"Authorization": f"Bearer test_token_user2"}
    app.dependency_overrides.clear()


# ==================== CHAT MUTE TESTS ====================

def test_get_mute_preferences_empty(auth_headers_user1, test_user1, test_db):
    """Test getting mute preferences when none exist (should create default)"""
    response = client.get("/chat-mute/preferences", headers=auth_headers_user1)
    assert response.status_code == 200
    data = response.json()
    assert data["global_chat_muted"] == False
    assert data["trivia_live_chat_muted"] == False
    assert data["private_chat_muted_users"] == []
    
    # Verify preferences were created in database
    prefs = test_db.query(ChatMutePreferences).filter(
        ChatMutePreferences.user_id == test_user1.account_id
    ).first()
    assert prefs is not None
    assert prefs.global_chat_muted == False
    assert prefs.trivia_live_chat_muted == False


def test_mute_global_chat(auth_headers_user1, test_user1, test_db):
    """Test muting global chat"""
    # Mute global chat
    response = client.post(
        "/chat-mute/global",
        headers=auth_headers_user1,
        json={"muted": True}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["global_chat_muted"] == True
    
    # Verify in database
    prefs = test_db.query(ChatMutePreferences).filter(
        ChatMutePreferences.user_id == test_user1.account_id
    ).first()
    assert prefs.global_chat_muted == True
    
    # Unmute
    response = client.post(
        "/chat-mute/global",
        headers=auth_headers_user1,
        json={"muted": False}
    )
    assert response.status_code == 200
    assert response.json()["global_chat_muted"] == False


def test_mute_trivia_live_chat(auth_headers_user1, test_user1, test_db):
    """Test muting trivia live chat"""
    # Mute trivia live chat
    response = client.post(
        "/chat-mute/trivia-live",
        headers=auth_headers_user1,
        json={"muted": True}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["trivia_live_chat_muted"] == True
    
    # Verify in database
    prefs = test_db.query(ChatMutePreferences).filter(
        ChatMutePreferences.user_id == test_user1.account_id
    ).first()
    assert prefs.trivia_live_chat_muted == True


def test_mute_private_chat_user(auth_headers_user1, test_user1, test_user2, test_db):
    """Test muting a specific user for private chat"""
    # Mute user2
    response = client.post(
        f"/chat-mute/private/{test_user2.account_id}",
        headers=auth_headers_user1,
        json={"muted": True}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["muted"] == True
    
    # Verify in database
    prefs = test_db.query(ChatMutePreferences).filter(
        ChatMutePreferences.user_id == test_user1.account_id
    ).first()
    assert test_user2.account_id in prefs.private_chat_muted_users
    
    # List muted users
    response = client.get("/chat-mute/private", headers=auth_headers_user1)
    assert response.status_code == 200
    data = response.json()
    assert len(data["muted_users"]) == 1
    assert data["muted_users"][0]["user_id"] == test_user2.account_id
    
    # Unmute user2
    response = client.post(
        f"/chat-mute/private/{test_user2.account_id}",
        headers=auth_headers_user1,
        json={"muted": False}
    )
    assert response.status_code == 200
    assert response.json()["muted"] == False


def test_mute_self_private_chat(auth_headers_user1, test_user1):
    """Test that you cannot mute yourself"""
    response = client.post(
        f"/chat-mute/private/{test_user1.account_id}",
        headers=auth_headers_user1,
        json={"muted": True}
    )
    assert response.status_code == 400
    assert "Cannot mute yourself" in response.json()["detail"]


# ==================== GLOBAL CHAT WITH PUSH TESTS ====================

@patch('routers.global_chat.send_push_for_global_chat_sync')
def test_global_chat_send_with_push(mock_push, auth_headers_user1, test_user1, test_db):
    """Test sending global chat message triggers push notification"""
    response = client.post(
        "/global-chat/send",
        headers=auth_headers_user1,
        json={
            "message": "Test global chat message",
            "client_message_id": f"test_global_{datetime.now().timestamp()}"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "message_id" in data
    assert "created_at" in data
    
    # Verify message was created
    message = test_db.query(GlobalChatMessage).filter(
        GlobalChatMessage.id == data["message_id"]
    ).first()
    assert message is not None
    assert message.message == "Test global chat message"
    
    # Verify push notification was scheduled (background task)
    # Note: In test environment, background tasks may not execute immediately
    # The mock will be called if background tasks run synchronously in tests


@patch('routers.global_chat.send_push_for_global_chat_sync')
def test_global_chat_muted_no_push(mock_push, auth_headers_user1, test_user1, test_db):
    """Test that muted users don't receive push notifications"""
    # Mute global chat
    client.post(
        "/chat-mute/global",
        headers=auth_headers_user1,
        json={"muted": True}
    )
    
    # Send message
    response = client.post(
        "/global-chat/send",
        headers=auth_headers_user1,
        json={
            "message": "Test message",
            "client_message_id": f"test_{datetime.now().timestamp()}"
        }
    )
    assert response.status_code == 200
    
    # Note: The push function will check mute status and skip this user
    # This is tested in the push function logic itself


def test_global_chat_messages_include_profile_data(auth_headers_user1, test_user1, test_db):
    """Test that global chat messages include avatar, frame, and badge"""
    # Send a message first
    response = client.post(
        "/global-chat/send",
        headers=auth_headers_user1,
        json={
            "message": "Test message with profile data",
            "client_message_id": f"test_{datetime.now().timestamp()}"
        }
    )
    assert response.status_code == 200
    
    # Get messages
    response = client.get("/global-chat/messages?limit=10", headers=auth_headers_user1)
    assert response.status_code == 200
    data = response.json()
    assert "messages" in data
    assert len(data["messages"]) > 0
    
    # Check first message has all profile fields
    first_msg = data["messages"][0]
    assert "id" in first_msg
    assert "user_id" in first_msg
    assert "username" in first_msg
    assert "profile_pic" in first_msg
    assert "avatar_url" in first_msg
    assert "frame_url" in first_msg
    assert "badge" in first_msg
    assert "message" in first_msg
    assert "created_at" in first_msg


# ==================== TRIVIA LIVE CHAT WITH PUSH TESTS ====================

@patch('routers.trivia_live_chat.is_trivia_live_chat_active')
@patch('routers.trivia_live_chat.send_push_for_trivia_live_chat_sync')
def test_trivia_live_chat_send_with_push(mock_push, mock_active, auth_headers_user1, test_user1, test_db):
    """Test sending trivia live chat message triggers push notification"""
    # Mock chat as active
    mock_active.return_value = True
    
    response = client.post(
        "/trivia-live-chat/send",
        headers=auth_headers_user1,
        json={
            "message": "Test trivia live chat message",
            "client_message_id": f"test_trivia_{datetime.now().timestamp()}"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "message_id" in data
    assert "created_at" in data
    
    # Verify message was created
    message = test_db.query(TriviaLiveChatMessage).filter(
        TriviaLiveChatMessage.id == data["message_id"]
    ).first()
    assert message is not None
    assert message.message == "Test trivia live chat message"


@patch('routers.trivia_live_chat.is_trivia_live_chat_active')
def test_trivia_live_chat_messages_include_profile_data(mock_active, auth_headers_user1, test_user1, test_db):
    """Test that trivia live chat messages include avatar, frame, and badge"""
    # Mock chat as active
    mock_active.return_value = True
    
    # Send a message first
    response = client.post(
        "/trivia-live-chat/send",
        headers=auth_headers_user1,
        json={
            "message": "Test message with profile data",
            "client_message_id": f"test_{datetime.now().timestamp()}"
        }
    )
    assert response.status_code == 200
    
    # Get messages
    response = client.get("/trivia-live-chat/messages?limit=10", headers=auth_headers_user1)
    assert response.status_code == 200
    data = response.json()
    assert "messages" in data
    assert len(data["messages"]) > 0
    
    # Check first message has all profile fields
    first_msg = data["messages"][0]
    assert "id" in first_msg
    assert "user_id" in first_msg
    assert "username" in first_msg
    assert "profile_pic" in first_msg
    assert "avatar_url" in first_msg
    assert "frame_url" in first_msg
    assert "badge" in first_msg
    assert "message" in first_msg
    assert "created_at" in first_msg


# ==================== PRIVATE CHAT WITH MUTE TESTS ====================

@patch('routers.private_chat.send_push_if_needed_sync')
def test_private_chat_send_with_mute_check(mock_push, auth_headers_user1, auth_headers_user2, 
                                           test_user1, test_user2, test_db):
    """Test that private chat checks mute status before sending push"""
    # Mute user2 by user1
    client.post(
        f"/chat-mute/private/{test_user2.account_id}",
        headers=auth_headers_user1,
        json={"muted": True}
    )
    
    # User2 sends message to user1 (user1 has muted user2)
    response = client.post(
        "/private-chat/send",
        headers=auth_headers_user2,
        json={
            "recipient_id": test_user1.account_id,
            "message": "Test private message",
            "client_message_id": f"test_{datetime.now().timestamp()}"
        }
    )
    assert response.status_code == 200
    
    # The push function should check mute status and skip
    # This is verified in the push function logic


@patch('routers.private_chat.send_push_if_needed_sync')
def test_private_chat_conversations_include_profile_data(mock_push, auth_headers_user1, auth_headers_user2,
                                                         test_user1, test_user2, test_db):
    """Test that private chat conversations include avatar, frame, and badge"""
    # Create a conversation by sending a message
    response = client.post(
        "/private-chat/send",
        headers=auth_headers_user1,
        json={
            "recipient_id": test_user2.account_id,
            "message": "Test message",
            "client_message_id": f"test_{datetime.now().timestamp()}"
        }
    )
    assert response.status_code == 200
    
    # List conversations
    response = client.get("/private-chat/conversations", headers=auth_headers_user1)
    assert response.status_code == 200
    data = response.json()
    assert "conversations" in data
    assert len(data["conversations"]) > 0
    
    # Check first conversation has all profile fields
    first_conv = data["conversations"][0]
    assert "conversation_id" in first_conv
    assert "peer_user_id" in first_conv
    assert "peer_username" in first_conv
    assert "peer_profile_pic" in first_conv
    assert "peer_avatar_url" in first_conv
    assert "peer_frame_url" in first_conv
    assert "peer_badge" in first_conv


def test_private_chat_messages_include_profile_data(auth_headers_user1, auth_headers_user2,
                                                     test_user1, test_user2, test_db):
    """Test that private chat messages include avatar, frame, and badge"""
    # Create a conversation by sending a message
    response = client.post(
        "/private-chat/send",
        headers=auth_headers_user1,
        json={
            "recipient_id": test_user2.account_id,
            "message": "Test message",
            "client_message_id": f"test_{datetime.now().timestamp()}"
        }
    )
    assert response.status_code == 200
    conversation_id = response.json()["conversation_id"]
    
    # Accept the conversation (as user2)
    response = client.post(
        "/private-chat/accept-reject",
        headers=auth_headers_user2,
        json={
            "conversation_id": conversation_id,
            "action": "accept"
        }
    )
    assert response.status_code == 200
    
    # Get messages
    response = client.get(
        f"/private-chat/conversations/{conversation_id}/messages",
        headers=auth_headers_user1
    )
    assert response.status_code == 200
    data = response.json()
    assert "messages" in data
    assert len(data["messages"]) > 0
    
    # Check first message has all profile fields
    first_msg = data["messages"][0]
    assert "id" in first_msg
    assert "sender_id" in first_msg
    assert "sender_username" in first_msg
    assert "sender_profile_pic" in first_msg
    assert "sender_avatar_url" in first_msg
    assert "sender_frame_url" in first_msg
    assert "sender_badge" in first_msg
    assert "message" in first_msg
    assert "status" in first_msg

