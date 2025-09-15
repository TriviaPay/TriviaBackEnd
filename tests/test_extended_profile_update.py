"""
Test script for the extended profile update functionality with one-time username update restriction.
"""
import sys
import os
import json
import requests
from datetime import datetime

# Add the parent directory to the Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Local testing URL
BASE_URL = "http://localhost:8000"

def get_access_token():
    """
    Use the refresh token endpoint to get a valid access token.
    This is just a test function - in a real application you would use proper authentication.
    """
    try:
        print("Getting access token...")
        # This is a sample refresh token endpoint call
        refresh_response = requests.post(
            f"{BASE_URL}/auth/refresh",
            headers={"Content-Type": "application/json"},
            json={"refresh_token": "un1a6gAWzYI7h69usP7V5gL0ENqUq6j64Wzp_dPva7zEc"}
        )
        
        print(f"Refresh token response status: {refresh_response.status_code}")
        
        if refresh_response.status_code == 200:
            token_data = refresh_response.json()
            print("Successfully got access token")
            return token_data.get("access_token")
        else:
            print(f"Failed to get access token: {refresh_response.status_code}")
            print(refresh_response.text)
            return None
    except Exception as e:
        print(f"Error getting access token: {str(e)}")
        return None

def test_extended_profile_update():
    """
    Test the extended profile update endpoint with the one-time username update restriction.
    """
    # Get access token
    print("\n=== Starting Extended Profile Update Test ===")
    access_token = get_access_token()
    if not access_token:
        print("Could not get access token. Exiting.")
        return
    
    # Set up headers
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }
    
    # First update - should succeed
    print("\n1. Testing first profile update:")
    first_update_data = {
        "username": f"testuser_{int(datetime.now().timestamp())}",  # Unique username
        "first_name": "Test",
        "last_name": "User",
        "mobile": "123-456-7890",
        "gender": "Male",
        "street_1": "123 Test St",
        "city": "Test City",
        "state": "Test State",
        "zip": "12345",
        "country": "USA"
    }
    
    print(f"Sending request to {BASE_URL}/profile/extended-update")
    print(f"Request data: {json.dumps(first_update_data, indent=2)}")
    
    try:
        response = requests.post(
            f"{BASE_URL}/profile/extended-update",
            headers=headers,
            json=first_update_data
        )
        
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print(f"Response: {json.dumps(response.json(), indent=2)}")
        else:
            print(f"Error response: {response.text}")
        
        # If first update succeeded, try to update username again
        if response.status_code == 200 and response.json().get("status") == "success":
            # Second update with new username - should fail due to restriction
            print("\n2. Testing second username update (should fail):")
            second_update_data = {
                "username": f"newusername_{int(datetime.now().timestamp())}",  # Another unique username
                "first_name": "Updated",
                "last_name": "User"
            }
            
            print(f"Request data: {json.dumps(second_update_data, indent=2)}")
            
            response = requests.post(
                f"{BASE_URL}/profile/extended-update",
                headers=headers,
                json=second_update_data
            )
            
            print(f"Status Code: {response.status_code}")
            if response.status_code == 200:
                print(f"Response: {json.dumps(response.json(), indent=2)}")
            else:
                print(f"Error response: {response.text}")
            
            # Third update without username - should succeed
            print("\n3. Testing update without username change (should succeed):")
            third_update_data = {
                "first_name": "Final",
                "last_name": "Update",
                "gender": "Other"
            }
            
            print(f"Request data: {json.dumps(third_update_data, indent=2)}")
            
            response = requests.post(
                f"{BASE_URL}/profile/extended-update",
                headers=headers,
                json=third_update_data
            )
            
            print(f"Status Code: {response.status_code}")
            if response.status_code == 200:
                print(f"Response: {json.dumps(response.json(), indent=2)}")
            else:
                print(f"Error response: {response.text}")
    except requests.exceptions.ConnectionError:
        print(f"ERROR: Could not connect to {BASE_URL}. Is the server running?")
    except Exception as e:
        print(f"Unexpected error during testing: {str(e)}")

if __name__ == "__main__":
    print("Testing Extended Profile Update API")
    test_extended_profile_update()
    print("\n=== Test Completed ===") 