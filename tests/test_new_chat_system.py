import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta, date
from sqlalchemy.orm import Session

from main import app
from models import (
    User, GlobalChatMessage, PrivateChatConversation, PrivateChatMessage,
    TriviaLiveChatMessage, OneSignalPlayer, Block, PrivateChatStatus, MessageStatus
)
from routers.dependencies import get_current_user
from routers.global_chat import router as global_chat_router
from routers.private_chat import router as private_chat_router
from routers.trivia_live_chat import router as trivia_live_chat_router
from routers.onesignal import router as onesignal_router
from routers.pusher_auth import router as pusher_auth_router
from utils.draw_calculations import get_next_draw_time
import pytz
import os

client = TestClient(app)


# ==================== FIXTURES ====================

@pytest.fixture
def test_user1(test_db):
    """Create first test user"""
    import uuid
    unique_id = str(uuid.uuid4())[:8]
    user = User(
        email=f"chat_user1_{unique_id}@test.com",
        username=f"chatuser1_{unique_id}",
        descope_user_id=f"test_descope_chat_1_{unique_id}"
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
        email=f"chat_user2_{unique_id}@test.com",
        username=f"chatuser2_{unique_id}",
        descope_user_id=f"test_descope_chat_2_{unique_id}"
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture
def test_user3(test_db):
    """Create third test user"""
    import uuid
    unique_id = str(uuid.uuid4())[:8]
    user = User(
        email=f"chat_user3_{unique_id}@test.com",
        username=f"chatuser3_{unique_id}",
        descope_user_id=f"test_descope_chat_3_{unique_id}"
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture
def auth_override_user1(test_user1):
    """Override auth to return user1"""
    app.dependency_overrides[get_current_user] = lambda: test_user1
    yield test_user1
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def auth_override_user2(test_user2):
    """Override auth to return user2"""
    app.dependency_overrides[get_current_user] = lambda: test_user2
    yield test_user2
    app.dependency_overrides.pop(get_current_user, None)


# ==================== GLOBAL CHAT TESTS ====================

class TestGlobalChat:
    """Test Global Chat endpoints"""
    
    def test_send_global_message(self, test_db, auth_override_user1):
        """Test sending a message to global chat"""
        with patch('routers.global_chat.publish_chat_message_sync') as mock_pusher:
            response = client.post(
                "/global-chat/send",
                json={
                    "message": "Hello global chat!",
                    "client_message_id": "test_msg_1"
                }
            )
            
            assert response.status_code == 200
            data = response.json()
            assert "message_id" in data
            assert data["duplicate"] == False
            
            # Verify message was saved
            message = test_db.query(GlobalChatMessage).filter(
                GlobalChatMessage.id == data["message_id"]
            ).first()
            assert message is not None
            assert message.message == "Hello global chat!"
            assert message.user_id == auth_override_user1.account_id
    
    def test_send_global_message_idempotency(self, test_db, auth_override_user1):
        """Test idempotency with duplicate client_message_id"""
        # Send first message
        response1 = client.post(
            "/global-chat/send",
            json={
                "message": "First message",
                "client_message_id": "duplicate_test"
            }
        )
        assert response1.status_code == 200
        message_id_1 = response1.json()["message_id"]
        
        # Send duplicate
        response2 = client.post(
            "/global-chat/send",
            json={
                "message": "Second message",
                "client_message_id": "duplicate_test"
            }
        )
        assert response2.status_code == 200
        data = response2.json()
        assert data["message_id"] == message_id_1
        assert data["duplicate"] == True
        
        # Verify only one message exists
        messages = test_db.query(GlobalChatMessage).filter(
            GlobalChatMessage.client_message_id == "duplicate_test"
        ).all()
        assert len(messages) == 1
    
    def test_get_global_messages(self, test_db, auth_override_user1, test_user2):
        """Test retrieving global chat messages"""
        # Create some messages
        for i in range(5):
            msg = GlobalChatMessage(
                user_id=test_user2.account_id if i % 2 == 0 else auth_override_user1.account_id,
                message=f"Test message {i}"
            )
            test_db.add(msg)
        test_db.commit()
        
        response = client.get("/global-chat/messages?limit=10")
        assert response.status_code == 200
        data = response.json()
        assert "messages" in data
        assert len(data["messages"]) == 5
    
    def test_global_chat_rate_limiting(self, test_db, auth_override_user1):
        """Test rate limiting for global chat"""
        with patch('routers.global_chat.GLOBAL_CHAT_MAX_MESSAGES_PER_MINUTE', 2):
            # Send 2 messages (should succeed)
            for i in range(2):
                response = client.post(
                    "/global-chat/send",
                    json={"message": f"Message {i}"}
                )
                assert response.status_code == 200
            
            # Third message should be rate limited
            response = client.post(
                "/global-chat/send",
                json={"message": "Rate limited message"}
            )
            assert response.status_code == 429


# ==================== PRIVATE CHAT TESTS ====================

class TestPrivateChat:
    """Test Private Chat endpoints"""
    
    def test_send_private_message_creates_conversation(self, test_db, auth_override_user1, test_user2):
        """Test sending first message creates conversation"""
        with patch('routers.private_chat.publish_chat_message_sync'), \
             patch('routers.private_chat.send_push_if_needed_sync'):
            
            response = client.post(
                "/private-chat/send",
                json={
                    "recipient_id": test_user2.account_id,
                    "message": "Hello!",
                    "client_message_id": "private_msg_1"
                }
            )
            
            assert response.status_code == 200
            data = response.json()
            assert "conversation_id" in data
            assert "message_id" in data
            assert data["status"] == "pending"
            
            # Verify conversation was created
            conversation = test_db.query(PrivateChatConversation).filter(
                PrivateChatConversation.id == data["conversation_id"]
            ).first()
            assert conversation is not None
            assert conversation.status == PrivateChatStatus.PENDING
            assert conversation.requested_by == auth_override_user1.account_id
    
    def test_send_private_message_auto_accepts(self, test_db, auth_override_user1, test_user2):
        """Test that recipient sending message auto-accepts conversation"""
        # User1 sends first message (creates pending conversation)
        app.dependency_overrides[get_current_user] = lambda: auth_override_user1
        with patch('routers.private_chat.publish_chat_message_sync'), \
             patch('routers.private_chat.send_push_if_needed_sync'):
            response1 = client.post(
                "/private-chat/send",
                json={
                    "recipient_id": test_user2.account_id,
                    "message": "First message"
                }
            )
            conversation_id = response1.json()["conversation_id"]
        
        # User2 sends message (should auto-accept)
        app.dependency_overrides[get_current_user] = lambda: test_user2
        with patch('routers.private_chat.publish_chat_message_sync'), \
             patch('routers.private_chat.send_push_if_needed_sync'):
            response2 = client.post(
                "/private-chat/send",
                json={
                    "recipient_id": auth_override_user1.account_id,
                    "message": "Response message"
                }
            )
            
            assert response2.status_code == 200
            assert response2.json()["status"] == "accepted"
            
            # Verify conversation is accepted
            conversation = test_db.query(PrivateChatConversation).filter(
                PrivateChatConversation.id == conversation_id
            ).first()
            assert conversation.status == PrivateChatStatus.ACCEPTED
    
    def test_accept_reject_chat_request(self, test_db, auth_override_user1, test_user2):
        """Test accepting/rejecting chat request"""
        # User1 creates conversation
        app.dependency_overrides[get_current_user] = lambda: auth_override_user1
        with patch('routers.private_chat.publish_chat_message_sync'), \
             patch('routers.private_chat.send_push_if_needed_sync'):
            response = client.post(
                "/private-chat/send",
                json={
                    "recipient_id": test_user2.account_id,
                    "message": "Request message"
                }
            )
            conversation_id = response.json()["conversation_id"]
        
        # User2 accepts
        app.dependency_overrides[get_current_user] = lambda: test_user2
        with patch('routers.private_chat.publish_chat_message_sync'):
            response = client.post(
                "/private-chat/accept-reject",
                json={
                    "conversation_id": conversation_id,
                    "action": "accept"
                }
            )
            
            assert response.status_code == 200
            assert response.json()["status"] == "accepted"
            
            conversation = test_db.query(PrivateChatConversation).filter(
                PrivateChatConversation.id == conversation_id
            ).first()
            assert conversation.status == PrivateChatStatus.ACCEPTED
    
    def test_reject_chat_request(self, test_db, auth_override_user1, test_user2):
        """Test rejecting chat request"""
        # User1 creates conversation
        app.dependency_overrides[get_current_user] = lambda: auth_override_user1
        with patch('routers.private_chat.publish_chat_message_sync'), \
             patch('routers.private_chat.send_push_if_needed_sync'):
            response = client.post(
                "/private-chat/send",
                json={
                    "recipient_id": test_user2.account_id,
                    "message": "Request message"
                }
            )
            conversation_id = response.json()["conversation_id"]
        
        # User2 rejects
        app.dependency_overrides[get_current_user] = lambda: test_user2
        with patch('routers.private_chat.publish_chat_message_sync'):
            response = client.post(
                "/private-chat/accept-reject",
                json={
                    "conversation_id": conversation_id,
                    "action": "reject"
                }
            )
            
            assert response.status_code == 200
            assert response.json()["status"] == "rejected"
            
            # Try to send message after rejection (should fail)
            app.dependency_overrides[get_current_user] = lambda: auth_override_user1
            response = client.post(
                "/private-chat/send",
                json={
                    "recipient_id": test_user2.account_id,
                    "message": "Another message"
                }
            )
            assert response.status_code == 403
            assert "not accepting private messages" in response.json()["detail"].lower()
    
    def test_list_private_conversations(self, test_db, auth_override_user1, test_user2, test_user3):
        """Test listing private conversations"""
        # Create conversations
        conv1 = PrivateChatConversation(
            user1_id=min(auth_override_user1.account_id, test_user2.account_id),
            user2_id=max(auth_override_user1.account_id, test_user2.account_id),
            status=PrivateChatStatus.ACCEPTED,
            requested_by=auth_override_user1.account_id
        )
        conv2 = PrivateChatConversation(
            user1_id=min(auth_override_user1.account_id, test_user3.account_id),
            user2_id=max(auth_override_user1.account_id, test_user3.account_id),
            status=PrivateChatStatus.ACCEPTED,
            requested_by=auth_override_user1.account_id
        )
        test_db.add_all([conv1, conv2])
        test_db.commit()
        
        # Add messages to conv1
        msg = PrivateChatMessage(
            conversation_id=conv1.id,
            sender_id=test_user2.account_id,
            message="Test message",
            status=MessageStatus.SENT
        )
        test_db.add(msg)
        conv1.last_message_at = datetime.utcnow()
        test_db.commit()
        
        response = client.get("/private-chat/conversations")
        assert response.status_code == 200
        data = response.json()
        assert "conversations" in data
        assert len(data["conversations"]) == 2
    
    def test_get_private_messages(self, test_db, auth_override_user1, test_user2):
        """Test getting messages from a conversation"""
        # Create conversation
        conv = PrivateChatConversation(
            user1_id=min(auth_override_user1.account_id, test_user2.account_id),
            user2_id=max(auth_override_user1.account_id, test_user2.account_id),
            status=PrivateChatStatus.ACCEPTED,
            requested_by=auth_override_user1.account_id
        )
        test_db.add(conv)
        test_db.commit()
        
        # Add messages
        for i in range(3):
            msg = PrivateChatMessage(
                conversation_id=conv.id,
                sender_id=test_user2.account_id if i % 2 == 0 else auth_override_user1.account_id,
                message=f"Message {i}",
                status=MessageStatus.SENT
            )
            test_db.add(msg)
        test_db.commit()
        
        response = client.get(f"/private-chat/conversations/{conv.id}/messages")
        assert response.status_code == 200
        data = response.json()
        assert "messages" in data
        assert len(data["messages"]) == 3
    
    def test_mark_conversation_read(self, test_db, auth_override_user1, test_user2):
        """Test marking conversation as read"""
        # Create conversation and messages
        conv = PrivateChatConversation(
            user1_id=min(auth_override_user1.account_id, test_user2.account_id),
            user2_id=max(auth_override_user1.account_id, test_user2.account_id),
            status=PrivateChatStatus.ACCEPTED,
            requested_by=auth_override_user1.account_id
        )
        test_db.add(conv)
        test_db.commit()
        
        messages = []
        for i in range(3):
            msg = PrivateChatMessage(
                conversation_id=conv.id,
                sender_id=test_user2.account_id,
                message=f"Message {i}",
                status=MessageStatus.SENT
            )
            test_db.add(msg)
            messages.append(msg)
        test_db.commit()
        
        # Mark as read up to last message
        with patch('routers.private_chat.publish_chat_message_sync'):
            response = client.post(
                f"/private-chat/conversations/{conv.id}/mark-read",
                params={"message_id": messages[-1].id}
            )
            
            assert response.status_code == 200
            data = response.json()
            assert data["last_read_message_id"] == messages[-1].id
            
            # Verify conversation was updated
            test_db.refresh(conv)
            if conv.user1_id == auth_override_user1.account_id:
                assert conv.last_read_message_id_user1 == messages[-1].id
            else:
                assert conv.last_read_message_id_user2 == messages[-1].id
    
    def test_private_chat_blocking(self, test_db, auth_override_user1, test_user2):
        """Test that blocked users cannot send messages"""
        # Create block
        block = Block(
            blocker_id=test_user2.account_id,
            blocked_id=auth_override_user1.account_id
        )
        test_db.add(block)
        test_db.commit()
        
        # Try to send message (should fail)
        response = client.post(
            "/private-chat/send",
            json={
                "recipient_id": test_user2.account_id,
                "message": "Blocked message"
            }
        )
        assert response.status_code == 403
        assert "blocked" in response.json()["detail"].lower()


# ==================== TRIVIA LIVE CHAT TESTS ====================

class TestTriviaLiveChat:
    """Test Trivia Live Chat endpoints"""
    
    @patch('routers.trivia_live_chat.is_trivia_live_chat_active')
    def test_send_trivia_live_message(self, mock_active, test_db, auth_override_user1):
        """Test sending message to trivia live chat"""
        mock_active.return_value = True
        
        with patch('routers.trivia_live_chat.publish_chat_message_sync') as mock_pusher:
            response = client.post(
                "/trivia-live-chat/send",
                json={
                    "message": "Trivia chat message!",
                    "client_message_id": "trivia_msg_1"
                }
            )
            
            assert response.status_code == 200
            data = response.json()
            assert "message_id" in data
            assert data["duplicate"] == False
            
            # Verify trivia message was saved
            trivia_msg = test_db.query(TriviaLiveChatMessage).filter(
                TriviaLiveChatMessage.id == data["message_id"]
            ).first()
            assert trivia_msg is not None
            assert trivia_msg.message == "Trivia chat message!"
            
            # Verify global message was NOT created (trivia and global are now separate)
            global_msg = test_db.query(GlobalChatMessage).filter(
                GlobalChatMessage.message == "Trivia chat message!"
            ).first()
            assert global_msg is None  # Should not exist
    
    @patch('routers.trivia_live_chat.is_trivia_live_chat_active')
    def test_trivia_live_chat_inactive(self, mock_active, test_db, auth_override_user1):
        """Test that trivia live chat rejects messages when inactive"""
        mock_active.return_value = False
        
        response = client.post(
            "/trivia-live-chat/send",
            json={"message": "Should fail"}
        )
        
        assert response.status_code == 403
        assert "not active" in response.json()["detail"].lower()
    
    @patch('routers.trivia_live_chat.is_trivia_live_chat_active')
    def test_get_trivia_live_messages(self, mock_active, test_db, auth_override_user1):
        """Test getting trivia live chat messages"""
        mock_active.return_value = True
        
        # Get next draw time
        next_draw = get_next_draw_time()
        draw_date = next_draw.astimezone(pytz.UTC).replace(tzinfo=None).date()
        
        # Create messages
        for i in range(3):
            msg = TriviaLiveChatMessage(
                user_id=auth_override_user1.account_id,
                message=f"Trivia message {i}",
                draw_date=draw_date
            )
            test_db.add(msg)
        test_db.commit()
        
        response = client.get("/trivia-live-chat/messages")
        assert response.status_code == 200
        data = response.json()
        assert "messages" in data
        assert data["is_active"] == True
    
    @patch('routers.trivia_live_chat.is_trivia_live_chat_active')
    def test_trivia_live_chat_status(self, mock_active):
        """Test trivia live chat status endpoint"""
        mock_active.return_value = True
        
        response = client.get("/trivia-live-chat/status")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] == True
        assert data["is_active"] == True
        assert "window_start" in data
        assert "window_end" in data


# ==================== ONESIGNAL TESTS ====================

class TestOneSignal:
    """Test OneSignal endpoints"""
    
    def test_register_player(self, test_db, auth_override_user1):
        """Test registering OneSignal player"""
        response = client.post(
            "/onesignal/register",
            json={
                "player_id": "test_player_123",
                "platform": "ios"
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Player registered"
        assert data["player_id"] == "test_player_123"
        
        # Verify player was saved
        player = test_db.query(OneSignalPlayer).filter(
            OneSignalPlayer.player_id == "test_player_123"
        ).first()
        assert player is not None
        assert player.user_id == auth_override_user1.account_id
        assert player.platform == "ios"
        assert player.is_valid == True
    
    def test_update_player(self, test_db, auth_override_user1):
        """Test updating existing player"""
        # Register first
        player = OneSignalPlayer(
            user_id=auth_override_user1.account_id,
            player_id="test_player_456",
            platform="android"
        )
        test_db.add(player)
        test_db.commit()
        
        # Update
        response = client.post(
            "/onesignal/register",
            json={
                "player_id": "test_player_456",
                "platform": "ios"
            }
        )
        
        assert response.status_code == 200
        assert response.json()["message"] == "Player updated"
        
        # Verify update
        test_db.refresh(player)
        assert player.platform == "ios"
    
    def test_list_players(self, test_db, auth_override_user1):
        """Test listing user's players"""
        # Create players
        for i, platform in enumerate(["ios", "android", "web"]):
            player = OneSignalPlayer(
                user_id=auth_override_user1.account_id,
                player_id=f"player_{i}",
                platform=platform
            )
            test_db.add(player)
        test_db.commit()
        
        response = client.get("/onesignal/players")
        assert response.status_code == 200
        data = response.json()
        assert "players" in data
        assert len(data["players"]) == 3


# ==================== PUSHER AUTH TESTS ====================

class TestPusherAuth:
    """Test Pusher authentication endpoint"""
    
    def test_pusher_auth_private_channel(self, test_db, auth_override_user1, test_user2):
        """Test Pusher auth for private conversation channel"""
        # Create conversation
        conv = PrivateChatConversation(
            user1_id=min(auth_override_user1.account_id, test_user2.account_id),
            user2_id=max(auth_override_user1.account_id, test_user2.account_id),
            status=PrivateChatStatus.ACCEPTED,
            requested_by=auth_override_user1.account_id
        )
        test_db.add(conv)
        test_db.commit()
        
        with patch('routers.pusher_auth.get_pusher_client') as mock_pusher:
            mock_client = MagicMock()
            mock_client.authenticate.return_value = {"auth": "test_auth_string"}
            mock_pusher.return_value = mock_client
            
            response = client.post(
                "/pusher/auth",
                data={
                    "socket_id": "123.456",
                    "channel_name": f"private-conversation-{conv.id}"
                }
            )
            
            assert response.status_code == 200
            mock_client.authenticate.assert_called_once()
    
    def test_pusher_auth_unauthorized_channel(self, test_db, auth_override_user1, test_user2, test_user3):
        """Test Pusher auth fails for unauthorized conversation"""
        # Create conversation between user1 and user2
        conv = PrivateChatConversation(
            user1_id=min(auth_override_user1.account_id, test_user2.account_id),
            user2_id=max(auth_override_user1.account_id, test_user2.account_id),
            status=PrivateChatStatus.ACCEPTED,
            requested_by=auth_override_user1.account_id
        )
        test_db.add(conv)
        test_db.commit()
        
        # User3 tries to access (should fail)
        app.dependency_overrides[get_current_user] = lambda: test_user3
        response = client.post(
            "/pusher/auth",
            data={
                "socket_id": "123.456",
                "channel_name": f"private-conversation-{conv.id}"
            }
        )
        
        assert response.status_code == 403
        app.dependency_overrides.pop(get_current_user, None)
    
    def test_pusher_auth_public_channel(self, auth_override_user1):
        """Test Pusher auth for public channel"""
        response = client.post(
            "/pusher/auth",
            data={
                "socket_id": "123.456",
                "channel_name": "global-chat"
            }
        )
        
        assert response.status_code == 200
        assert response.json()["status"] == "authorized"


# ==================== INTEGRATION TESTS ====================

class TestChatIntegration:
    """Integration tests for chat system"""
    
    def test_trivia_message_separate_from_global(self, test_db, auth_override_user1):
        """Test that trivia live chat messages are separate from global chat"""
        with patch('routers.trivia_live_chat.is_trivia_live_chat_active', return_value=True), \
             patch('routers.trivia_live_chat.publish_chat_message_sync'):
            
            response = client.post(
                "/trivia-live-chat/send",
                json={"message": "Trivia message"}
            )
            
            assert response.status_code == 200
            trivia_msg_id = response.json()["message_id"]
            
            # Check global chat - trivia messages should NOT appear in global chat
            response = client.get("/global-chat/messages")
            assert response.status_code == 200
            messages = response.json()["messages"]
            
            # Verify trivia message is NOT in global chat (they are now separate)
            trivia_msg_in_global = next((m for m in messages if m.get("id") == trivia_msg_id), None)
            assert trivia_msg_in_global is None  # Should not exist in global chat
    
    def test_private_chat_unread_count(self, test_db, auth_override_user1, test_user2):
        """Test unread count calculation"""
        # Create conversation
        conv = PrivateChatConversation(
            user1_id=min(auth_override_user1.account_id, test_user2.account_id),
            user2_id=max(auth_override_user1.account_id, test_user2.account_id),
            status=PrivateChatStatus.ACCEPTED,
            requested_by=auth_override_user1.account_id
        )
        test_db.add(conv)
        test_db.commit()
        
        # Add unread messages from user2
        for i in range(5):
            msg = PrivateChatMessage(
                conversation_id=conv.id,
                sender_id=test_user2.account_id,
                message=f"Unread {i}",
                status=MessageStatus.SENT
            )
            test_db.add(msg)
        test_db.commit()
        
        # Check unread count for user1
        response = client.get("/private-chat/conversations")
        assert response.status_code == 200
        conversations = response.json()["conversations"]
        conv_data = next(c for c in conversations if c["conversation_id"] == conv.id)
        assert conv_data["unread_count"] == 5
        
        # Mark as read
        last_msg = test_db.query(PrivateChatMessage).filter(
            PrivateChatMessage.conversation_id == conv.id
        ).order_by(PrivateChatMessage.id.desc()).first()
        
        with patch('routers.private_chat.publish_chat_message_sync'):
            client.post(
                f"/private-chat/conversations/{conv.id}/mark-read",
                params={"message_id": last_msg.id}
            )
        
        # Check unread count again (should be 0)
        response = client.get("/private-chat/conversations")
        conversations = response.json()["conversations"]
        conv_data = next(c for c in conversations if c["conversation_id"] == conv.id)
        assert conv_data["unread_count"] == 0

