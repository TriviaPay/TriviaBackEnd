import requests
import json
import time

def test_refresh_endpoint():
    """Test the refresh endpoint with a valid token"""
    
    base_url = "http://localhost:8000"
    
    # First, let's get a valid token by logging in
    print("🔐 Testing Refresh Endpoint with Valid Token")
    print("=" * 60)
    
    # Test with a valid token (you would need to get this from a real login)
    # For now, let's test the error cases
    
    print("\n1️⃣ Testing Refresh Endpoint Error Cases")
    print("-" * 40)
    
    # Test with no authorization header
    try:
        response = requests.post(f"{base_url}/auth/refresh")
        print(f"   No auth header: {response.status_code} - {response.text[:100]}")
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for missing auth header")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with no auth header: {e}")
    
    # Test with empty authorization header
    try:
        headers = {"Authorization": ""}
        response = requests.post(f"{base_url}/auth/refresh", headers=headers)
        print(f"   Empty auth header: {response.status_code} - {response.text[:100]}")
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for empty auth header")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with empty auth header: {e}")
    
    # Test with invalid token format
    try:
        headers = {"Authorization": "Bearer invalid_token"}
        response = requests.post(f"{base_url}/auth/refresh", headers=headers)
        print(f"   Invalid token: {response.status_code} - {response.text[:100]}")
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for invalid token")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with invalid token: {e}")
    
    # Test with expired token
    try:
        # Create an expired token
        import jwt
        import uuid
        
        expired_payload = {
            "iss": "https://triviapay.us.auth0.com/",
            "sub": f"email|{uuid.uuid4().hex[:16]}",
            "aud": ["https://triviapay.us.auth0.com/api/v2/"],
            "iat": int(time.time()) - 86400,  # 24 hours ago
            "exp": int(time.time()) - 3600,   # 1 hour ago
            "email": "test@example.com",
        }
        
        expired_token = jwt.encode(expired_payload, "test_secret", algorithm="HS256")
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = requests.post(f"{base_url}/auth/refresh", headers=headers)
        print(f"   Expired token: {response.status_code} - {response.text[:100]}")
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for expired token")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with expired token: {e}")
    
    print("\n2️⃣ Testing Refresh New Session Endpoint Error Cases")
    print("-" * 40)
    
    # Test refresh-new-session with no authorization header
    try:
        response = requests.post(f"{base_url}/auth/refresh-new-session")
        print(f"   No auth header: {response.status_code} - {response.text[:100]}")
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for missing auth header")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with no auth header: {e}")
    
    # Test refresh-new-session with expired token
    try:
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = requests.post(f"{base_url}/auth/refresh-new-session", headers=headers)
        print(f"   Expired token: {response.status_code} - {response.text[:100]}")
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for expired token")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with expired token: {e}")
    
    print("\n" + "=" * 60)
    print("🏁 Refresh Endpoint Testing Complete")
    print("=" * 60)
    print("\nNote: To test with a valid token, you would need to:")
    print("1. Log in through the frontend to get a valid Descope token")
    print("2. Use that token to test the refresh functionality")
    print("3. The refresh endpoint should return a new session token")

if __name__ == "__main__":
    test_refresh_endpoint() 