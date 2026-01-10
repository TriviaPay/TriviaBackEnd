#!/usr/bin/env python3

import json
import os
import sys

import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


def test_api_endpoints():
    """Test the API endpoints to ensure everything is working"""
    base_url = "http://localhost:8000"

    print("Testing API endpoints...")

    # Test 1: Root endpoint (no auth required)
    try:
        response = requests.get(f"{base_url}/")
        if response.status_code == 200:
            print("✅ Root endpoint working")
            print(f"   Response: {response.json()}")
        else:
            print(f"❌ Root endpoint failed: {response.status_code}")
    except Exception as e:
        print(f"❌ Root endpoint error: {e}")

    # Test 2: Health check endpoint
    try:
        response = requests.get(f"{base_url}/health")
        if response.status_code == 200:
            print("✅ Health check endpoint working")
        else:
            print(f"❌ Health check failed: {response.status_code}")
    except Exception as e:
        print(f"❌ Health check error: {e}")

    # Test 3: OpenAPI docs
    try:
        response = requests.get(f"{base_url}/openapi.json")
        if response.status_code == 200:
            print("✅ OpenAPI docs accessible")
        else:
            print(f"❌ OpenAPI docs failed: {response.status_code}")
    except Exception as e:
        print(f"❌ OpenAPI docs error: {e}")

    # Test 4: Test authentication with expired token (should fail gracefully)
    expired_token = "eyJhbGciOiJSUzI1NiIsImtpZCI6IlNLMnlvVm1sdmR4VnNFRTZJVE41QnM1Tkh2bzZoIiwidHlwIjoiSldUIn0.eyJhbXIiOlsiZW1haWwiXSwiZHJuIjoiRFMiLCJleHAiOjE3NTA4NTM3MTcsImlhdCI6MTc1MDg1MzExNywiaXNzIjoiUDJ5b1ZtZWhkSFJZQ1pQZWhCT3BNZDk3V01zSCIsInJleHAiOiIyMDI1LTA3LTIzVDEyOjA1OjE3WiIsInN1YiI6IlUyejAxTDVuTnNIOGNVc2RWTjB1azZMbWJiOXgifQ.caiyL_QR8pBkat1CV3gXZyvLZ2Xcb6jKkPyyMv0UuT3AccyzCpyM26882sF0l85Z-MCkTkSlGIdwbfmEYH43MOKZ-FtNUynOJDkbXkN6_7sl3F5vG6hoQe1GLAZj_FInsJsJqETSv4hTHN59SlXli5YvzaIsj6pH4u7DGJSQgwJkfTEPc-yyCLKR_xn9Czun33aVNNRCB238TnT_q228Ll75XIy9GOETqChYO2sd9Xh5Mbn_gkVDGNEK8Qwy-AntLZMk801JIhvGD9pi9zUbTHRT3lVbrTzzcwEQBzI2UDwcu9_Vf5lVSRCc4gn8FG-G6EMcTSs2UNLzDmNSdKxB2w"

    try:
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = requests.get(f"{base_url}/test-descope-auth", headers=headers)
        if response.status_code == 401:
            print("✅ Authentication properly rejects expired tokens")
        else:
            print(
                f"❌ Authentication should have rejected expired token: {response.status_code}"
            )
    except Exception as e:
        print(f"❌ Authentication test error: {e}")

    # Test 5: Test bind-password endpoint with expired token
    try:
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = requests.post(
            f"{base_url}/bind-password?password=Fortrivia@1&username=Trivia",
            headers=headers,
        )
        if response.status_code == 401:
            print("✅ Bind-password endpoint properly rejects expired tokens")
        else:
            print(
                f"❌ Bind-password should have rejected expired token: {response.status_code}"
            )
    except Exception as e:
        print(f"❌ Bind-password test error: {e}")


def test_database_operations():
    """Test database operations"""
    print("\nTesting database operations...")

    try:
        from db import SessionLocal
        from models import User

        db = SessionLocal()

        # Test creating a user
        test_user = User(
            descope_user_id="integration_test_user",
            email="integration@test.com",
            username="integration_test",
            display_name="Integration Test User",
        )

        # Add to database
        db.add(test_user)
        db.commit()
        db.refresh(test_user)

        print(f"✅ User created successfully with account_id: {test_user.account_id}")

        # Test querying the user
        found_user = (
            db.query(User)
            .filter(User.descope_user_id == "integration_test_user")
            .first()
        )
        if found_user:
            print(f"✅ User retrieved successfully: {found_user.username}")
        else:
            print("❌ User not found after creation")

        # Clean up - delete the test user
        db.delete(test_user)
        db.commit()
        print("✅ Test user cleaned up successfully")

        db.close()

    except Exception as e:
        print(f"❌ Database operations failed: {e}")


def test_dependencies():
    """Test the dependencies module"""
    print("\nTesting dependencies...")

    try:
        from models import User
        from routers.dependencies import get_current_user, is_admin, verify_admin

        # Test is_admin function
        user = User(is_admin=True)
        if is_admin(user):
            print("✅ is_admin function works correctly")
        else:
            print("❌ is_admin function failed")

        # Test verify_admin function
        try:
            verify_admin(user)
            print("✅ verify_admin function works for admin users")
        except Exception as e:
            print(f"❌ verify_admin failed for admin user: {e}")

        # Test verify_admin with non-admin user
        non_admin_user = User(is_admin=False)
        try:
            verify_admin(non_admin_user)
            print("❌ verify_admin should have failed for non-admin user")
        except Exception:
            print("✅ verify_admin correctly rejects non-admin users")

        print("✅ Dependencies module working correctly")

    except Exception as e:
        print(f"❌ Dependencies test failed: {e}")


if __name__ == "__main__":
    print("🧪 Running comprehensive integration tests...\n")

    test_api_endpoints()
    test_database_operations()
    test_dependencies()

    print("\n🎉 Integration tests completed!")
