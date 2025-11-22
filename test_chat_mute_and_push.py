"""
Test script for chat mute preferences and push notifications.
Tests all endpoints created after separating global chat and trivia live chat.
"""
import requests
import json
from datetime import datetime

# Configuration
BASE_URL = "http://127.0.0.1:8000"
# You'll need to replace these with actual tokens from your test users
USER1_TOKEN = "YOUR_USER1_TOKEN_HERE"
USER2_TOKEN = "YOUR_USER2_TOKEN_HERE"

headers_user1 = {
    "Authorization": f"Bearer {USER1_TOKEN}",
    "Content-Type": "application/json"
}

headers_user2 = {
    "Authorization": f"Bearer {USER2_TOKEN}",
    "Content-Type": "application/json"
}


def print_response(title, response):
    """Helper to print formatted response"""
    print(f"\n{'='*60}")
    print(f"{title}")
    print(f"{'='*60}")
    print(f"Status: {response.status_code}")
    try:
        print(f"Response: {json.dumps(response.json(), indent=2)}")
    except:
        print(f"Response: {response.text}")


def test_chat_mute_endpoints():
    """Test chat mute preference endpoints"""
    print("\n" + "="*60)
    print("TESTING CHAT MUTE ENDPOINTS")
    print("="*60)
    
    # 1. Get initial preferences
    response = requests.get(f"{BASE_URL}/chat-mute/preferences", headers=headers_user1)
    print_response("1. GET /chat-mute/preferences", response)
    
    # 2. Mute global chat
    response = requests.post(
        f"{BASE_URL}/chat-mute/global",
        headers=headers_user1,
        json={"muted": True}
    )
    print_response("2. POST /chat-mute/global (mute)", response)
    
    # 3. Verify global chat is muted
    response = requests.get(f"{BASE_URL}/chat-mute/preferences", headers=headers_user1)
    print_response("3. GET /chat-mute/preferences (verify mute)", response)
    
    # 4. Mute trivia live chat
    response = requests.post(
        f"{BASE_URL}/chat-mute/trivia-live",
        headers=headers_user1,
        json={"muted": True}
    )
    print_response("4. POST /chat-mute/trivia-live (mute)", response)
    
    # 5. Unmute global chat
    response = requests.post(
        f"{BASE_URL}/chat-mute/global",
        headers=headers_user1,
        json={"muted": False}
    )
    print_response("5. POST /chat-mute/global (unmute)", response)
    
    # 6. Mute a user for private chat (need user2's ID)
    # Replace USER2_ID with actual user ID
    USER2_ID = 1234567890  # Replace with actual user ID
    response = requests.post(
        f"{BASE_URL}/chat-mute/private/{USER2_ID}",
        headers=headers_user1,
        json={"muted": True}
    )
    print_response(f"6. POST /chat-mute/private/{USER2_ID} (mute user)", response)
    
    # 7. List muted users
    response = requests.get(f"{BASE_URL}/chat-mute/private", headers=headers_user1)
    print_response("7. GET /chat-mute/private (list muted users)", response)
    
    # 8. Unmute user
    response = requests.post(
        f"{BASE_URL}/chat-mute/private/{USER2_ID}",
        headers=headers_user1,
        json={"muted": False}
    )
    print_response(f"8. POST /chat-mute/private/{USER2_ID} (unmute user)", response)


def test_global_chat_with_push():
    """Test global chat endpoints (push notifications are sent in background)"""
    print("\n" + "="*60)
    print("TESTING GLOBAL CHAT ENDPOINTS")
    print("="*60)
    
    # 1. Send a message to global chat
    response = requests.post(
        f"{BASE_URL}/global-chat/send",
        headers=headers_user1,
        json={
            "message": "Test global chat message with push notification",
            "client_message_id": f"test_global_{datetime.now().timestamp()}"
        }
    )
    print_response("1. POST /global-chat/send", response)
    
    if response.status_code == 200:
        message_data = response.json()
        message_id = message_data.get("message_id")
        print(f"\nMessage ID: {message_id}")
        print("Note: Push notifications are sent in background to all users (except sender)")
        print("      who are not muted and not active (30-second threshold)")
    
    # 2. Get global chat messages
    response = requests.get(
        f"{BASE_URL}/global-chat/messages?limit=10",
        headers=headers_user1
    )
    print_response("2. GET /global-chat/messages", response)
    
    # 3. Verify messages include avatar, frame, and badge
    if response.status_code == 200:
        data = response.json()
        messages = data.get("messages", [])
        if messages:
            first_msg = messages[0]
            print(f"\nFirst message fields:")
            print(f"  - id: {first_msg.get('id')}")
            print(f"  - user_id: {first_msg.get('user_id')}")
            print(f"  - username: {first_msg.get('username')}")
            print(f"  - profile_pic: {first_msg.get('profile_pic')}")
            print(f"  - avatar_url: {first_msg.get('avatar_url')}")
            print(f"  - frame_url: {first_msg.get('frame_url')}")
            print(f"  - badge: {first_msg.get('badge')}")
            print(f"  - message: {first_msg.get('message')}")
            print(f"  - created_at: {first_msg.get('created_at')}")
            print(f"  - online_count: {data.get('online_count')}")


def test_trivia_live_chat_with_push():
    """Test trivia live chat endpoints (push notifications are sent in background)"""
    print("\n" + "="*60)
    print("TESTING TRIVIA LIVE CHAT ENDPOINTS")
    print("="*60)
    
    # 1. Check trivia live chat status
    response = requests.get(f"{BASE_URL}/trivia-live-chat/status", headers=headers_user1)
    print_response("1. GET /trivia-live-chat/status", response)
    
    if response.status_code == 200:
        status = response.json()
        is_active = status.get("is_active", False)
        print(f"\nTrivia live chat is active: {is_active}")
        
        if not is_active:
            print("Note: Trivia live chat is not active. Messages can only be sent during active window.")
            return
    
    # 2. Send a message to trivia live chat
    response = requests.post(
        f"{BASE_URL}/trivia-live-chat/send",
        headers=headers_user1,
        json={
            "message": "Test trivia live chat message with push notification",
            "client_message_id": f"test_trivia_{datetime.now().timestamp()}"
        }
    )
    print_response("2. POST /trivia-live-chat/send", response)
    
    if response.status_code == 200:
        message_data = response.json()
        message_id = message_data.get("message_id")
        print(f"\nMessage ID: {message_id}")
        print("Note: Push notifications are sent in background to all users (except sender)")
        print("      who are not muted and not active (30-second threshold)")
    
    # 3. Get trivia live chat messages
    response = requests.get(
        f"{BASE_URL}/trivia-live-chat/messages?limit=10",
        headers=headers_user1
    )
    print_response("3. GET /trivia-live-chat/messages", response)
    
    # 4. Verify messages include avatar, frame, and badge
    if response.status_code == 200:
        data = response.json()
        messages = data.get("messages", [])
        if messages:
            first_msg = messages[0]
            print(f"\nFirst message fields:")
            print(f"  - id: {first_msg.get('id')}")
            print(f"  - user_id: {first_msg.get('user_id')}")
            print(f"  - username: {first_msg.get('username')}")
            print(f"  - profile_pic: {first_msg.get('profile_pic')}")
            print(f"  - avatar_url: {first_msg.get('avatar_url')}")
            print(f"  - frame_url: {first_msg.get('frame_url')}")
            print(f"  - badge: {first_msg.get('badge')}")
            print(f"  - message: {first_msg.get('message')}")
            print(f"  - created_at: {first_msg.get('created_at')}")
            print(f"  - viewer_count: {data.get('viewer_count')}")


def test_private_chat_with_mute():
    """Test private chat endpoints with mute checking"""
    print("\n" + "="*60)
    print("TESTING PRIVATE CHAT ENDPOINTS WITH MUTE")
    print("="*60)
    
    # Get user2's ID (you'll need to get this from your test setup)
    USER2_ID = 1234567890  # Replace with actual user ID
    
    # 1. Send a private message (creates conversation if needed)
    response = requests.post(
        f"{BASE_URL}/private-chat/send",
        headers=headers_user1,
        json={
            "recipient_id": USER2_ID,
            "message": "Test private message with mute checking",
            "client_message_id": f"test_private_{datetime.now().timestamp()}"
        }
    )
    print_response("1. POST /private-chat/send", response)
    
    if response.status_code == 200:
        data = response.json()
        conversation_id = data.get("conversation_id")
        print(f"\nConversation ID: {conversation_id}")
        print("Note: Push notification will be sent if user2 is not muted, not active, and has OneSignal player")
    
    # 2. List private conversations
    response = requests.get(f"{BASE_URL}/private-chat/conversations", headers=headers_user1)
    print_response("2. GET /private-chat/conversations", response)
    
    # 3. Verify conversations include avatar, frame, and badge
    if response.status_code == 200:
        data = response.json()
        conversations = data.get("conversations", [])
        if conversations:
            first_conv = conversations[0]
            print(f"\nFirst conversation fields:")
            print(f"  - conversation_id: {first_conv.get('conversation_id')}")
            print(f"  - peer_user_id: {first_conv.get('peer_user_id')}")
            print(f"  - peer_username: {first_conv.get('peer_username')}")
            print(f"  - peer_profile_pic: {first_conv.get('peer_profile_pic')}")
            print(f"  - peer_avatar_url: {first_conv.get('peer_avatar_url')}")
            print(f"  - peer_frame_url: {first_conv.get('peer_frame_url')}")
            print(f"  - peer_badge: {first_conv.get('peer_badge')}")
            print(f"  - unread_count: {first_conv.get('unread_count')}")


def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("CHAT MUTE AND PUSH NOTIFICATION TEST SUITE")
    print("="*60)
    print("\nNOTE: You need to:")
    print("1. Replace USER1_TOKEN and USER2_TOKEN with actual JWT tokens")
    print("2. Replace USER2_ID with actual user ID")
    print("3. Make sure the server is running on http://127.0.0.1:8000")
    print("4. Ensure test users have OneSignal players registered for push tests")
    
    input("\nPress Enter to continue with tests...")
    
    try:
        # Test chat mute endpoints
        test_chat_mute_endpoints()
        
        # Test global chat with push
        test_global_chat_with_push()
        
        # Test trivia live chat with push
        test_trivia_live_chat_with_push()
        
        # Test private chat with mute
        test_private_chat_with_mute()
        
        print("\n" + "="*60)
        print("ALL TESTS COMPLETED")
        print("="*60)
        print("\nNote: Push notifications are sent asynchronously in background tasks.")
        print("      Check your OneSignal dashboard to verify push notifications were sent.")
        print("      Check server logs for push notification delivery status.")
        
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

