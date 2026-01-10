#!/usr/bin/env python3

import json
import time
from datetime import datetime

import requests


def test_auth_endpoints():
    """Test all authentication-related endpoints end-to-end"""
    base_url = "http://localhost:8000"

    print("🔐 Testing Authentication Endpoints End-to-End")
    print("=" * 60)

    # Test tokens (expired and malformed)
    expired_token = "eyJhbGciOiJSUzI1NiIsImtpZCI6IlNLMnlvVm1sdmR4VnNFRTZJVE41QnM1Tkh2bzZoIiwidHlwIjoiSldUIn0.eyJhbXIiOlsiZW1haWwiXSwiZHJuIjoiRFMiLCJleHAiOjE3NTA4NTM3MTcsImlhdCI6MTc1MDg1MzExNywiaXNzIjoiUDJ5b1ZtZWhkSFJZQ1pQZWhCT3BNZDk3V01zSCIsInJleHAiOiIyMDI1LTA3LTIzVDEyOjA1OjE3WiIsInN1YiI6IlUyejAxTDVuTnNIOGNVc2RWTjB1azZMbWJiOXgifQ.caiyL_QR8pBkat1CV3gXZyvLZ2Xcb6jKkPyyMv0UuT3AccyzCpyM26882sF0l85Z-MCkTkSlGIdwbfmEYH43MOKZ-FtNUynOJDkbXkN6_7sl3F5vG6hoQe1GLAZj_FInsJsJqETSv4hTHN59SlXli5YvzaIsj6pH4u7DGJSQgwJkfTEPc-yyCLKR_xn9Czun33aVNNRCB238TnT_q228Ll75XIy9GOETqChYO2sd9Xh5Mbn_gkVDGNEK8Qwy-AntLZMk801JIhvGD9pi9zUbTHRT3lVbrTzzcwEQBzI2UDwcu9_Vf5lVSRCc4gn8FG-G6EMcTSs2UNLzDmNSdKxB2w"
    malformed_token = "invalid.token.here"
    empty_token = ""

    # Test 1: Test Descope Authentication Endpoint
    print("\n1️⃣ Testing /test-descope-auth endpoint")
    print("-" * 40)

    # Test with expired token
    try:
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = requests.get(f"{base_url}/test-descope-auth", headers=headers)
        print(f"   Expired token: {response.status_code} - {response.text[:100]}")
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for expired token")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with expired token: {e}")

    # Test with malformed token
    try:
        headers = {"Authorization": f"Bearer {malformed_token}"}
        response = requests.get(f"{base_url}/test-descope-auth", headers=headers)
        print(f"   Malformed token: {response.status_code} - {response.text[:100]}")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with malformed token: {e}")

    # Test with no token
    try:
        response = requests.get(f"{base_url}/test-descope-auth")
        print(f"   No token: {response.status_code} - {response.text[:100]}")
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for missing token")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with no token: {e}")

    # Test 2: Bind Password Endpoint
    print("\n2️⃣ Testing /bind-password endpoint")
    print("-" * 40)

    # Test with expired token
    try:
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = requests.post(
            f"{base_url}/bind-password?password=Fortrivia@1&username=Trivia",
            headers=headers,
        )
        print(f"   Expired token: {response.status_code} - {response.text[:100]}")
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for expired token")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with expired token: {e}")

    # Test with malformed token
    try:
        headers = {"Authorization": f"Bearer {malformed_token}"}
        response = requests.post(
            f"{base_url}/bind-password?password=Fortrivia@1&username=Trivia",
            headers=headers,
        )
        print(f"   Malformed token: {response.status_code} - {response.text[:100]}")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with malformed token: {e}")

    # Test with no token
    try:
        response = requests.post(
            f"{base_url}/bind-password?password=Fortrivia@1&username=Trivia"
        )
        print(f"   No token: {response.status_code} - {response.text[:100]}")
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for missing token")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with no token: {e}")

    # Test 3: Auth Test Token Endpoint
    print("\n3️⃣ Testing /auth/test-token endpoint")
    print("-" * 40)

    # Test with expired token
    try:
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = requests.post(f"{base_url}/auth/test-token", headers=headers)
        print(f"   Expired token: {response.status_code} - {response.text[:100]}")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with expired token: {e}")

    # Test with malformed token
    try:
        headers = {"Authorization": f"Bearer {malformed_token}"}
        response = requests.post(f"{base_url}/auth/test-token", headers=headers)
        print(f"   Malformed token: {response.status_code} - {response.text[:100]}")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with malformed token: {e}")

    # Test 4: Profile Endpoints (Protected)
    print("\n4️⃣ Testing Protected Profile Endpoints")
    print("-" * 40)

    # Test profile update with expired token
    try:
        headers = {"Authorization": f"Bearer {expired_token}"}
        profile_data = {
            "username": "testuser",
            "date_of_birth": "1990-01-01",
            "country": "US",
        }
        response = requests.post(
            f"{base_url}/profile/update", json=profile_data, headers=headers
        )
        print(
            f"   Profile update with expired token: {response.status_code} - {response.text[:100]}"
        )
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for expired token")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with expired token: {e}")

    # Test username check with expired token
    try:
        headers = {"Authorization": f"Bearer {expired_token}"}
        username_data = {"username": "testuser"}
        response = requests.post(
            f"{base_url}/profile/check-username", json=username_data, headers=headers
        )
        print(
            f"   Username check with expired token: {response.status_code} - {response.text[:100]}"
        )
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for expired token")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with expired token: {e}")

    # Test 5: Admin Endpoints (Protected)
    print("\n5️⃣ Testing Protected Admin Endpoints")
    print("-" * 40)

    # Test admin endpoint with expired token
    try:
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = requests.get(f"{base_url}/admin/draw-config", headers=headers)
        print(
            f"   Admin endpoint with expired token: {response.status_code} - {response.text[:100]}"
        )
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for expired token")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with expired token: {e}")

    # Test 6: Wallet Endpoints (Protected)
    print("\n6️⃣ Testing Protected Wallet Endpoints")
    print("-" * 40)

    # Test wallet balance with expired token
    try:
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = requests.get(f"{base_url}/wallet/balance", headers=headers)
        print(
            f"   Wallet balance with expired token: {response.status_code} - {response.text[:100]}"
        )
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for expired token")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with expired token: {e}")

    # Test 7: Trivia Endpoints (Protected)
    print("\n7️⃣ Testing Protected Trivia Endpoints")
    print("-" * 40)

    # Test trivia questions with expired token
    try:
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = requests.get(f"{base_url}/trivia/questions", headers=headers)
        print(
            f"   Trivia questions with expired token: {response.status_code} - {response.text[:100]}"
        )
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for expired token")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with expired token: {e}")

    # Test 8: Store Endpoints (Protected)
    print("\n8️⃣ Testing Protected Store Endpoints")
    print("-" * 40)

    # Test store items with expired token
    try:
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = requests.get(f"{base_url}/store/items", headers=headers)
        print(
            f"   Store items with expired token: {response.status_code} - {response.text[:100]}"
        )
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for expired token")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with expired token: {e}")

    # Test 9: Stripe Endpoints (Protected)
    print("\n9️⃣ Testing Protected Stripe Endpoints")
    print("-" * 40)

    # Test stripe payment methods with expired token
    try:
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = requests.get(f"{base_url}/stripe/payment-methods", headers=headers)
        print(
            f"   Stripe payment methods with expired token: {response.status_code} - {response.text[:100]}"
        )
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for expired token")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with expired token: {e}")

    # Test stripe create customer with expired token
    try:
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = requests.post(f"{base_url}/stripe/create-customer", headers=headers)
        print(
            f"   Stripe create customer with expired token: {response.status_code} - {response.text[:100]}"
        )
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for expired token")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with expired token: {e}")

    # Test refresh endpoint with expired token
    try:
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = requests.post(f"{base_url}/auth/refresh", headers=headers)
        print(
            f"   Refresh endpoint with expired token: {response.status_code} - {response.text[:100]}"
        )
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for expired token")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with expired token: {e}")

    # Test refresh-new-session endpoint with expired token
    try:
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = requests.post(
            f"{base_url}/auth/refresh-new-session", headers=headers
        )
        print(
            f"   Refresh new session with expired token: {response.status_code} - {response.text[:100]}"
        )
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for expired token")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with expired token: {e}")

    # Test 10: Error Cases
    print("\n🔟 Testing Error Cases")
    print("-" * 40)

    # Test with invalid Authorization header format
    try:
        headers = {"Authorization": "InvalidFormat token"}
        response = requests.get(f"{base_url}/test-descope-auth", headers=headers)
        print(f"   Invalid auth format: {response.status_code} - {response.text[:100]}")
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for invalid auth format")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with invalid auth format: {e}")

    # Test with empty Authorization header
    try:
        headers = {"Authorization": ""}
        response = requests.get(f"{base_url}/test-descope-auth", headers=headers)
        print(f"   Empty auth header: {response.status_code} - {response.text[:100]}")
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for empty auth header")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with empty auth header: {e}")

    # Test with missing Authorization header
    try:
        headers = {}
        response = requests.get(f"{base_url}/test-descope-auth", headers=headers)
        print(f"   Missing auth header: {response.status_code} - {response.text[:100]}")
        if response.status_code != 401:
            print("   ❌ ERROR: Should return 401 for missing auth header")
    except Exception as e:
        print(f"   ❌ ERROR: Exception with missing auth header: {e}")

    print("\n" + "=" * 60)
    print("🏁 Authentication Endpoint Testing Complete")
    print("=" * 60)


if __name__ == "__main__":
    test_auth_endpoints()
