import json
import time
import uuid
from datetime import datetime, timedelta

import jwt
import requests


def test_complete_auth_flow():
    """Test the complete authentication flow with Descope"""

    base_url = "http://localhost:8000"

    print("🔐 Testing Complete Authentication Flow")
    print("=" * 60)

    # Test 1: Test with a valid Descope token (simulated)
    print("\n1️⃣ Testing with Valid Descope Token")
    print("-" * 40)

    # Use the real Descope session token provided by the user
    valid_token = "eyJhbGciOiJSUzI1NiIsImtpZCI6IlNLMnlvVm1sdmR4VnNFRTZJVE41QnM1Tkh2bzZoIiwidHlwIjoiSldUIn0.eyJhbXIiOlsiZW1haWwiXSwiZHJuIjoiRFMiLCJleHAiOjE3NTA4NTM3MTcsImlhdCI6MTc1MDg1MzExNywiaXNzIjoiUDJ5b1ZtZWhkSFJZQ1pQZWhCT3BNZDk3V01zSCIsInJleHAiOiIyMDI1LTA3LTIzVDEyOjA1OjE3WiIsInN1YiI6IlUyejAxTDVuTnNIOGNVc2RWTjB1azZMbWJiOXgifQ.caiyL_QR8pBkat1CV3gXZyvLZ2Xcb6jKkPyyMv0UuT3AccyzCpyM26882sF0l85Z-MCkTkSlGIdwbfmEYH43MOKZ-FtNUynOJDkbXkN6_7sl3F5vG6hoQe1GLAZj_FInsJsJqETSv4hTHN59SlXli5YvzaIsj6pH4u7DGJSQgwJkfTEPc-yyCLKR_xn9Czun33aVNNRCB238TnT_q228Ll75XIy9GOETqChYO2sd9Xh5Mbn_gkVDGNEK8Qwy-AntLZMk801JIhvGD9pi9zUbTHRT3lVbrTzzcwEQBzI2UDwcu9_Vf5lVSRCc4gn8FG-G6EMcTSs2UNLzDmNSdKxB2w"
    headers = {"Authorization": f"Bearer {valid_token}"}

    print(f"   Created test token: {valid_token[:50]}...")

    # Test 2: Test protected endpoints with valid token
    print("\n2️⃣ Testing Protected Endpoints with Valid Token")
    print("-" * 40)

    # Test profile endpoints
    try:
        response = requests.get(f"{base_url}/profile/complete", headers=headers)
        print(f"   Profile endpoint: {response.status_code} - {response.text[:100]}")
    except Exception as e:
        print(f"   ❌ ERROR: Profile endpoint failed: {e}")

    # Test wallet endpoints
    try:
        response = requests.get(f"{base_url}/wallet/balance", headers=headers)
        print(f"   Wallet balance: {response.status_code} - {response.text[:100]}")
    except Exception as e:
        print(f"   ❌ ERROR: Wallet endpoint failed: {e}")

    # Test trivia endpoints
    try:
        response = requests.get(f"{base_url}/trivia/questions", headers=headers)
        print(f"   Trivia questions: {response.status_code} - {response.text[:100]}")
    except Exception as e:
        print(f"   ❌ ERROR: Trivia endpoint failed: {e}")

    # Test store endpoints
    try:
        response = requests.get(f"{base_url}/store/items", headers=headers)
        print(f"   Store items: {response.status_code} - {response.text[:100]}")
    except Exception as e:
        print(f"   ❌ ERROR: Store endpoint failed: {e}")

    # Test 3: Test refresh endpoint with valid token
    print("\n3️⃣ Testing Refresh Endpoint with Valid Token")
    print("-" * 40)

    try:
        response = requests.post(f"{base_url}/auth/refresh", headers=headers)
        print(f"   Refresh endpoint: {response.status_code} - {response.text[:100]}")
        if response.status_code == 200:
            print("   ✅ Refresh successful!")
        else:
            print("   ❌ Refresh failed")
    except Exception as e:
        print(f"   ❌ ERROR: Refresh endpoint failed: {e}")

    # Test 4: Test refresh-new-session endpoint with valid token
    print("\n4️⃣ Testing Refresh New Session with Valid Token")
    print("-" * 40)

    try:
        response = requests.post(
            f"{base_url}/auth/refresh-new-session", headers=headers
        )
        print(f"   Refresh new session: {response.status_code} - {response.text[:100]}")
        if response.status_code == 200:
            print("   ✅ New session created!")
        else:
            print("   ❌ New session creation failed")
    except Exception as e:
        print(f"   ❌ ERROR: Refresh new session failed: {e}")

    # Test 5: Test with expired token
    print("\n5️⃣ Testing with Expired Token")
    print("-" * 40)

    # Create an expired token
    expired_payload = {
        "iss": "P2yoVmehdHRYCZPehBOpMd97WMsH",
        "sub": "U2z01L5nNsH8cUsdVN0uk6Lmbb9x",
        "aud": ["P2yoVmehdHRYCZPehBOpMd97WMsH"],
        "iat": int(time.time()) - 7200,  # 2 hours ago
        "exp": int(time.time()) - 3600,  # 1 hour ago
        "email": "test@example.com",
        "loginIds": ["test@example.com"],
        "name": "Test User",
        "displayName": "Test User",
    }

    expired_token = jwt.encode(expired_payload, "test_secret", algorithm="HS256")
    expired_headers = {"Authorization": f"Bearer {expired_token}"}

    # Test refresh with expired token
    try:
        response = requests.post(f"{base_url}/auth/refresh", headers=expired_headers)
        print(
            f"   Refresh with expired token: {response.status_code} - {response.text[:100]}"
        )
        if response.status_code == 401:
            print("   ✅ Correctly rejected expired token")
        else:
            print("   ❌ Should have rejected expired token")
    except Exception as e:
        print(f"   ❌ ERROR: Refresh with expired token failed: {e}")

    # Test refresh-new-session with expired token
    try:
        response = requests.post(
            f"{base_url}/auth/refresh-new-session", headers=expired_headers
        )
        print(
            f"   Refresh new session with expired token: {response.status_code} - {response.text[:100]}"
        )
        if response.status_code == 401:
            print("   ✅ Correctly rejected expired token")
        else:
            print("   ❌ Should have rejected expired token")
    except Exception as e:
        print(f"   ❌ ERROR: Refresh new session with expired token failed: {e}")

    # Test 6: Test error cases
    print("\n6️⃣ Testing Error Cases")
    print("-" * 40)

    # Test with no token
    try:
        response = requests.post(f"{base_url}/auth/refresh")
        print(f"   No token: {response.status_code} - {response.text[:100]}")
        if response.status_code == 401:
            print("   ✅ Correctly rejected missing token")
        else:
            print("   ❌ Should have rejected missing token")
    except Exception as e:
        print(f"   ❌ ERROR: No token test failed: {e}")

    # Test with invalid token format
    try:
        invalid_headers = {"Authorization": "Bearer invalid_token"}
        response = requests.post(f"{base_url}/auth/refresh", headers=invalid_headers)
        print(f"   Invalid token: {response.status_code} - {response.text[:100]}")
        if response.status_code == 401:
            print("   ✅ Correctly rejected invalid token")
        else:
            print("   ❌ Should have rejected invalid token")
    except Exception as e:
        print(f"   ❌ ERROR: Invalid token test failed: {e}")

    # Test 7: Test login endpoint
    print("\n7️⃣ Testing Login Endpoint")
    print("-" * 40)

    # Test login with Descope token (using bind-password endpoint)
    try:
        response = requests.post(
            f"{base_url}/bind-password?password=testpass123&username=testuser",
            headers=headers,
        )
        print(
            f"   Bind password with valid token: {response.status_code} - {response.text[:100]}"
        )
        if response.status_code == 200:
            print("   ✅ Bind password successful!")
        else:
            print("   ❌ Bind password failed")
    except Exception as e:
        print(f"   ❌ ERROR: Bind password test failed: {e}")

    # Test login with expired token
    try:
        response = requests.post(
            f"{base_url}/bind-password?password=testpass123&username=testuser",
            headers=expired_headers,
        )
        print(
            f"   Bind password with expired token: {response.status_code} - {response.text[:100]}"
        )
        if response.status_code == 401:
            print("   ✅ Correctly rejected expired token for bind password")
        else:
            print("   ❌ Should have rejected expired token for bind password")
    except Exception as e:
        print(f"   ❌ ERROR: Bind password with expired token test failed: {e}")

    # Test 8: Test test-descope-auth endpoint
    print("\n8️⃣ Testing Test Descope Auth Endpoint")
    print("-" * 40)

    try:
        response = requests.get(f"{base_url}/test-descope-auth", headers=headers)
        print(
            f"   Test descope auth with valid token: {response.status_code} - {response.text[:100]}"
        )
        if response.status_code == 200:
            print("   ✅ Test descope auth successful!")
        else:
            print("   ❌ Test descope auth failed")
    except Exception as e:
        print(f"   ❌ ERROR: Test descope auth test failed: {e}")

    try:
        response = requests.get(
            f"{base_url}/test-descope-auth", headers=expired_headers
        )
        print(
            f"   Test descope auth with expired token: {response.status_code} - {response.text[:100]}"
        )
        if response.status_code == 401:
            print("   ✅ Correctly rejected expired token for test descope auth")
        else:
            print("   ❌ Should have rejected expired token for test descope auth")
    except Exception as e:
        print(f"   ❌ ERROR: Test descope auth with expired token test failed: {e}")

    print("\n" + "=" * 60)
    print("🏁 Complete Authentication Flow Testing Complete")
    print("=" * 60)
    print("\nNote: Some tests may fail because we're using simulated tokens.")
    print(
        "In a real environment, you would use actual Descope tokens from the frontend."
    )


if __name__ == "__main__":
    test_complete_auth_flow()
