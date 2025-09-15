#!/usr/bin/env python3

import os
from dotenv import load_dotenv
from descope.descope_client import DescopeClient
import logging

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Get Descope configuration
project_id = os.getenv("DESCOPE_PROJECT_ID")
management_key = os.getenv("DESCOPE_MANAGEMENT_KEY")

if not project_id:
    print("❌ DESCOPE_PROJECT_ID not found in environment variables")
    exit(1)

print(f"Project ID: {project_id}")
print(f"Management Key: {management_key[:10] if management_key else 'None'}...")

# Test token from the logs
test_token = "eyJhbGciOiJSUzI1NiIsImtpZCI6IlNLMnlvVm1sdmR4VnNFRTZJVE41QnM1Tkh2bzZoIiwidHlwIjoiSldUIn0.eyJhbXIiOlsiZW1haWwiXSwiZHJuIjoiRFMiLCJleHAiOjE3NTA4NTM3MTcsImlhdCI6MTc1MDg1MzExNywiaXNzIjoiUDJ5b1ZtZWhkSFJZQ1pQZWhCT3BNZDk3V01zSCIsInJleHAiOiIyMDI1LTA3LTIzVDEyOjA1OjE3WiIsInN1YiI6IlUyejAxTDVuTnNIOGNVc2RWTjB1azZMbWJiOXgifQ.caiyL_QR8pBkat1CV3gXZyvLZ2Xcb6jKkPyyMv0UuT3AccyzCpyM26882sF0l85Z-MCkTkSlGIdwbfmEYH43MOKZ-FtNUynOJDkbXkN6_7sl3F5vG6hoQe1GLAZj_FInsJsJqETSv4hTHN59SlXli5YvzaIsj6pH4u7DGJSQgwJkfTEPc-yyCLKR_xn9Czun33aVNNRCB238TnT_q228Ll75XIy9GOETqChYO2sd9Xh5Mbn_gkVDGNEK8Qwy-AntLZMk801JIhvGD9pi9zUbTHRT3lVbrTzzcwEQBzI2UDwcu9_Vf5lVSRCc4gn8FG-G6EMcTSs2UNLzDmNSdKxB2w"

try:
    # Create client with project ID only and higher leeway
    client = DescopeClient(project_id=project_id, jwt_validation_leeway=300)
    print("Created Descope client successfully")
    
    # Try to validate the session
    print("Attempting to validate session...")
    session = client.validate_session(test_token)
    print("✅ Session validation successful!")
    print(f"User info: {session.get('user', {})}")
    
except Exception as e:
    print(f"❌ Session validation failed: {e}")
    print(f"Error type: {type(e).__name__}")
    
    # Try with management key
    if management_key:
        try:
            print("\nTrying with management key...")
            mgmt_client = DescopeClient(project_id=project_id, management_key=management_key, jwt_validation_leeway=300)
            session = mgmt_client.validate_session(test_token)
            print("✅ Session validation with management key successful!")
            print(f"User info: {session.get('user', {})}")
        except Exception as e2:
            print(f"❌ Session validation with management key also failed: {e2}")
            print(f"Error type: {type(e2).__name__}")
    else:
        print("\nSkipping management key test - no management key provided")

print("\nTesting dependencies function...")
try:
    from routers.dependencies import get_current_user
    from fastapi import Request
    from unittest.mock import Mock
    
    # Create a mock request
    mock_request = Mock()
    mock_request.headers = {"Authorization": f"Bearer {test_token}"}
    
    # This will test the dependencies without needing a full FastAPI app
    print("✅ Dependencies function imported successfully")
    
except Exception as e:
    print(f"❌ Dependencies test failed: {e}") 