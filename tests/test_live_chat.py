import pytest
import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import os

from main import app
from models import User, LiveChatSession, LiveChatMessage, LiveChatLike, LiveChatViewer
from routers.live_chat import ConnectionManager, is_chat_window_active, get_or_create_active_session
from config import LIVE_CHAT_ENABLED, LIVE_CHAT_PRE_DRAW_HOURS, LIVE_CHAT_POST_DRAW_HOURS

# Test client
client = TestClient(app)

class TestLiveChatModels:
    """Test live chat database models"""
    
    def test_live_chat_session_creation(self, db_session):
        """Test creating a live chat session"""
        session = LiveChatSession(
            session_name="Test Chat Session",
            start_time=datetime.utcnow(),
            end_time=datetime.utcnow() + timedelta(hours=2),
            is_active=True
        )
        
        db_session.add(session)
        db_session.commit()
        db_session.refresh(session)
        
        assert session.id is not None
        assert session.session_name == "Test Chat Session"
        assert session.is_active is True
        assert session.viewer_count == 0
        assert session.total_likes == 0
    
    def test_live_chat_message_creation(self, db_session, test_user):
        """Test creating a live chat message"""
        # Create session first
        session = LiveChatSession(
            session_name="Test Session",
            start_time=datetime.utcnow(),
            end_time=datetime.utcnow() + timedelta(hours=2),
            is_active=True
        )
        db_session.add(session)
        db_session.commit()
        db_session.refresh(session)
        
        # Create message
        message = LiveChatMessage(
            session_id=session.id,
            user_id=test_user.account_id,
            message="Hello, world!",
            message_type="text"
        )
        
        db_session.add(message)
        db_session.commit()
        db_session.refresh(message)
        
        assert message.id is not None
        assert message.message == "Hello, world!"
        assert message.message_type == "text"
        assert message.likes == 0
        assert message.user_id == test_user.account_id
        assert message.session_id == session.id
    
    def test_live_chat_like_creation(self, db_session, test_user):
        """Test creating a live chat like"""
        # Create session first
        session = LiveChatSession(
            session_name="Test Session",
            start_time=datetime.utcnow(),
            end_time=datetime.utcnow() + timedelta(hours=2),
            is_active=True
        )
        db_session.add(session)
        db_session.commit()
        db_session.refresh(session)
        
        # Create like
        like = LiveChatLike(
            session_id=session.id,
            user_id=test_user.account_id,
            message_id=None  # Session like
        )
        
        db_session.add(like)
        db_session.commit()
        db_session.refresh(like)
        
        assert like.id is not None
        assert like.session_id == session.id
        assert like.user_id == test_user.account_id
        assert like.message_id is None
    
    def test_live_chat_viewer_creation(self, db_session, test_user):
        """Test creating a live chat viewer"""
        # Create session first
        session = LiveChatSession(
            session_name="Test Session",
            start_time=datetime.utcnow(),
            end_time=datetime.utcnow() + timedelta(hours=2),
            is_active=True
        )
        db_session.add(session)
        db_session.commit()
        db_session.refresh(session)
        
        # Create viewer
        viewer = LiveChatViewer(
            session_id=session.id,
            user_id=test_user.account_id,
            is_active=True
        )
        
        db_session.add(viewer)
        db_session.commit()
        db_session.refresh(viewer)
        
        assert viewer.id is not None
        assert viewer.session_id == session.id
        assert viewer.user_id == test_user.account_id
        assert viewer.is_active is True

class TestConnectionManager:
    """Test WebSocket connection manager"""
    
    def test_connection_manager_init(self):
        """Test connection manager initialization"""
        manager = ConnectionManager()
        assert manager.active_connections == {}
        assert manager.user_sessions == {}
    
    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self):
        """Test connecting and disconnecting WebSocket"""
        manager = ConnectionManager()
        mock_websocket = MagicMock()
        
        # Test connect
        await manager.connect(mock_websocket, 123, 1)
        assert 1 in manager.active_connections
        assert len(manager.active_connections[1]) == 1
        assert manager.user_sessions[mock_websocket] == 123
        
        # Test disconnect
        manager.disconnect(mock_websocket, 1)
        assert len(manager.active_connections[1]) == 0
        assert mock_websocket not in manager.user_sessions

class TestLiveChatFunctions:
    """Test live chat utility functions"""
    
    @patch('routers.live_chat.LIVE_CHAT_ENABLED', False)
    def test_is_chat_window_active_disabled(self):
        """Test chat window check when disabled"""
        assert is_chat_window_active() is False
    
    @patch('routers.live_chat.LIVE_CHAT_ENABLED', True)
    @patch('routers.live_chat.get_next_draw_time')
    def test_is_chat_window_active_enabled(self, mock_get_next_draw_time):
        """Test chat window check when enabled"""
        # Mock draw time to be 30 minutes from now
        now = datetime.now()
        mock_draw_time = now + timedelta(minutes=30)
        mock_get_next_draw_time.return_value = mock_draw_time
        
        # Should be active (within 1 hour before draw)
        assert is_chat_window_active() is True
    
    @patch('routers.live_chat.LIVE_CHAT_ENABLED', True)
    @patch('routers.live_chat.get_next_draw_time')
    def test_is_chat_window_active_outside_window(self, mock_get_next_draw_time):
        """Test chat window check when outside window"""
        # Mock draw time to be 2 hours from now
        now = datetime.now()
        mock_draw_time = now + timedelta(hours=2)
        mock_get_next_draw_time.return_value = mock_draw_time
        
        # Should not be active (more than 1 hour before draw)
        assert is_chat_window_active() is False
    
    def test_get_or_create_active_session_existing(self, db_session):
        """Test getting existing active session"""
        # Create existing session
        existing_session = LiveChatSession(
            session_name="Existing Session",
            start_time=datetime.utcnow() - timedelta(minutes=30),
            end_time=datetime.utcnow() + timedelta(minutes=30),
            is_active=True
        )
        db_session.add(existing_session)
        db_session.commit()
        
        # Should return existing session
        session = get_or_create_active_session(db_session)
        assert session.id == existing_session.id
        assert session.session_name == "Existing Session"
    
    @patch('routers.live_chat.get_next_draw_time')
    def test_get_or_create_active_session_new(self, mock_get_next_draw_time, db_session):
        """Test creating new active session"""
        # Mock draw time
        now = datetime.now()
        mock_draw_time = now + timedelta(minutes=30)
        mock_get_next_draw_time.return_value = mock_draw_time
        
        # Should create new session
        session = get_or_create_active_session(db_session)
        assert session.id is not None
        assert "Draw Chat" in session.session_name
        assert session.is_active is True

class TestLiveChatEndpoints:
    """Test live chat REST endpoints"""
    
    def test_get_chat_status_disabled(self):
        """Test getting chat status when disabled"""
        with patch('routers.live_chat.LIVE_CHAT_ENABLED', False):
            response = client.get("/live-chat/status")
            assert response.status_code == 200
            data = response.json()
            assert data["enabled"] is False
            assert "disabled" in data["message"]
    
    def test_get_chat_status_enabled_inactive(self):
        """Test getting chat status when enabled but inactive"""
        with patch('routers.live_chat.LIVE_CHAT_ENABLED', True), \
             patch('routers.live_chat.is_chat_window_active', return_value=False):
            
            response = client.get("/live-chat/status")
            assert response.status_code == 200
            data = response.json()
            assert data["enabled"] is True
            assert data["is_active"] is False
            assert data["session"] is None
    
    def test_get_chat_status_enabled_active(self, test_user_token):
        """Test getting chat status when enabled and active"""
        with patch('routers.live_chat.LIVE_CHAT_ENABLED', True), \
             patch('routers.live_chat.is_chat_window_active', return_value=True), \
             patch('routers.live_chat.get_or_create_active_session') as mock_get_session:
            
            # Mock session
            mock_session = MagicMock()
            mock_session.id = 1
            mock_session.session_name = "Test Session"
            mock_session.viewer_count = 5
            mock_session.total_likes = 10
            mock_session.start_time = datetime.utcnow()
            mock_session.end_time = datetime.utcnow() + timedelta(hours=1)
            mock_get_session.return_value = mock_session
            
            response = client.get("/live-chat/status", headers={"Authorization": f"Bearer {test_user_token}"})
            assert response.status_code == 200
            data = response.json()
            assert data["enabled"] is True
            assert data["is_active"] is True
            assert data["session"]["id"] == 1
            assert data["session"]["viewer_count"] == 5
            assert data["session"]["total_likes"] == 10
    
    def test_get_chat_messages_disabled(self, test_user_token):
        """Test getting messages when chat is disabled"""
        with patch('routers.live_chat.LIVE_CHAT_ENABLED', False):
            response = client.get("/live-chat/messages", headers={"Authorization": f"Bearer {test_user_token}"})
            assert response.status_code == 403
            assert "not active" in response.json()["detail"]
    
    def test_get_chat_messages_enabled(self, test_user_token):
        """Test getting messages when chat is enabled"""
        with patch('routers.live_chat.LIVE_CHAT_ENABLED', True), \
             patch('routers.live_chat.is_chat_window_active', return_value=True), \
             patch('routers.live_chat.get_or_create_active_session') as mock_get_session:
            
            # Mock session and messages
            mock_session = MagicMock()
            mock_session.id = 1
            mock_get_session.return_value = mock_session
            
            # Mock database query
            with patch('routers.live_chat.db') as mock_db:
                mock_message = MagicMock()
                mock_message.id = 1
                mock_message.user_id = 123
                mock_message.message = "Test message"
                mock_message.message_type = "text"
                mock_message.likes = 0
                mock_message.created_at = datetime.utcnow()
                mock_message.user.username = "testuser"
                mock_message.user.profile_pic_url = None
                mock_message.user.badge_id = None
                mock_message.user.is_admin = False
                
                mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [mock_message]
                
                response = client.get("/live-chat/messages", headers={"Authorization": f"Bearer {test_user_token}"})
                assert response.status_code == 200
                data = response.json()
                assert "messages" in data
                assert len(data["messages"]) == 1
                assert data["messages"][0]["message"] == "Test message"
    
    def test_like_session_disabled(self, test_user_token):
        """Test liking session when chat is disabled"""
        with patch('routers.live_chat.LIVE_CHAT_ENABLED', False):
            response = client.post("/live-chat/like", headers={"Authorization": f"Bearer {test_user_token}"})
            assert response.status_code == 403
            assert "not active" in response.json()["detail"]
    
    def test_like_message_disabled(self, test_user_token):
        """Test liking message when chat is disabled"""
        with patch('routers.live_chat.LIVE_CHAT_ENABLED', False):
            response = client.post("/live-chat/like-message/1", headers={"Authorization": f"Bearer {test_user_token}"})
            assert response.status_code == 403
            assert "not active" in response.json()["detail"]

class TestLiveChatWebSocket:
    """Test WebSocket functionality"""
    
    @pytest.mark.asyncio
    async def test_websocket_connection_disabled(self):
        """Test WebSocket connection when chat is disabled"""
        with patch('routers.live_chat.LIVE_CHAT_ENABLED', False):
            with client.websocket_connect("/live-chat/ws/1?token=invalid") as websocket:
                # Should close immediately
                pass
    
    @pytest.mark.asyncio
    async def test_websocket_connection_invalid_token(self):
        """Test WebSocket connection with invalid token"""
        with patch('routers.live_chat.LIVE_CHAT_ENABLED', True), \
             patch('routers.live_chat.is_chat_window_active', return_value=True):
            
            with client.websocket_connect("/live-chat/ws/1?token=invalid") as websocket:
                # Should close due to invalid token
                pass

# Integration tests
class TestLiveChatIntegration:
    """Integration tests for live chat functionality"""
    
    def test_full_chat_flow(self, test_user_token, db_session):
        """Test complete chat flow"""
        with patch('routers.live_chat.LIVE_CHAT_ENABLED', True), \
             patch('routers.live_chat.is_chat_window_active', return_value=True):
            
            # 1. Check status
            response = client.get("/live-chat/status", headers={"Authorization": f"Bearer {test_user_token}"})
            assert response.status_code == 200
            
            # 2. Get messages
            response = client.get("/live-chat/messages", headers={"Authorization": f"Bearer {test_user_token}"})
            assert response.status_code == 200
            
            # 3. Like session
            response = client.post("/live-chat/like", headers={"Authorization": f"Bearer {test_user_token}"})
            assert response.status_code == 200
            assert "liked successfully" in response.json()["message"]
    
    def test_rate_limiting(self, test_user_token):
        """Test message rate limiting"""
        with patch('routers.live_chat.LIVE_CHAT_ENABLED', True), \
             patch('routers.live_chat.is_chat_window_active', return_value=True), \
             patch('routers.live_chat.LIVE_CHAT_MAX_MESSAGES_PER_USER_PER_MINUTE', 1):
            
            # This would need WebSocket testing to fully test rate limiting
            # For now, just test that the configuration is respected
            assert LIVE_CHAT_MAX_MESSAGES_PER_USER_PER_MINUTE == 1

# Fixtures
@pytest.fixture
def test_user(db_session):
    """Create a test user"""
    user = User(
        email="test@example.com",
        username="testuser",
        descope_user_id="test_descope_id"
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user

@pytest.fixture
def test_user_token():
    """Mock test user token"""
    return "mock_jwt_token"

@pytest.fixture
def db_session():
    """Create database session for testing"""
    from db import SessionLocal
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

# Configuration for testing
@pytest.fixture(autouse=True)
def setup_test_config():
    """Setup test configuration"""
    os.environ["LIVE_CHAT_ENABLED"] = "true"
    os.environ["LIVE_CHAT_PRE_DRAW_HOURS"] = "1"
    os.environ["LIVE_CHAT_POST_DRAW_HOURS"] = "1"
    os.environ["LIVE_CHAT_MAX_MESSAGES_PER_USER_PER_MINUTE"] = "10"
    os.environ["LIVE_CHAT_MESSAGE_HISTORY_LIMIT"] = "100"
