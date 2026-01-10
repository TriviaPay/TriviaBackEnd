import json
import os
from datetime import date, datetime, timedelta

import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Base URL for API
BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

# Admin email and password (these should be set in environment variables)
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "triviapay3@gmail.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# JWT token placeholder
admin_token = None


def get_admin_token():
    """Get a JWT token for admin access"""
    global admin_token

    # This depends on your actual authentication mechanism
    # This is just a placeholder - replace with your actual authentication method
    auth_url = f"{BASE_URL}/login"
    auth_data = {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}

    response = requests.post(auth_url, json=auth_data)
    if response.status_code == 200:
        result = response.json()
        admin_token = result.get("access_token")
        return admin_token
    else:
        print(f"Authentication failed: {response.status_code} - {response.text}")
        return None


def get_headers():
    """Get the headers with authentication token"""
    if admin_token is None:
        get_admin_token()

    return {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json",
    }


def test_get_draw_config():
    """Test getting the draw configuration"""
    print("\n=== Testing get draw configuration ===")
    response = requests.get(f"{BASE_URL}/admin/draw-config", headers=get_headers())

    if response.status_code == 200:
        result = response.json()
        print("Draw configuration:")
        print(f"  Is custom: {result['is_custom']}")
        print(f"  Custom winner count: {result['custom_winner_count']}")
        print(
            f"  Draw time: {result['draw_time_hour']}:{result['draw_time_minute']} {result['draw_timezone']}"
        )
    else:
        print(
            f"Failed to get draw configuration: {response.status_code} - {response.text}"
        )


def test_update_draw_config():
    """Test updating the draw configuration"""
    print("\n=== Testing update draw configuration ===")

    # Set a custom winner count
    config_data = {"is_custom": True, "custom_winner_count": 5}

    response = requests.put(
        f"{BASE_URL}/admin/draw-config", headers=get_headers(), json=config_data
    )

    if response.status_code == 200:
        result = response.json()
        print("Updated draw configuration:")
        print(f"  Is custom: {result['is_custom']}")
        print(f"  Custom winner count: {result['custom_winner_count']}")
        print(
            f"  Draw time: {result['draw_time_hour']}:{result['draw_time_minute']} {result['draw_timezone']}"
        )
    else:
        print(
            f"Failed to update draw configuration: {response.status_code} - {response.text}"
        )


def test_trigger_draw():
    """Test manually triggering a draw"""
    print("\n=== Testing trigger draw ===")

    # Set the draw date to yesterday to avoid conflicts with existing draws
    yesterday = date.today() - timedelta(days=1)
    draw_data = {"draw_date": yesterday.isoformat()}

    response = requests.post(
        f"{BASE_URL}/admin/trigger-draw", headers=get_headers(), json=draw_data
    )

    if response.status_code == 200:
        result = response.json()
        print(f"Draw triggered for {result['draw_date']}:")
        print(f"  Status: {result['status']}")
        print(f"  Participants: {result['total_participants']}")
        print(f"  Winners: {result['total_winners']}")
        print(f"  Prize pool: ${result['prize_pool']}")

        if result["winners"]:
            print("\nWinners:")
            for winner in result["winners"]:
                print(
                    f"  Position {winner['position']}: {winner['username']} - ${winner['prize_amount']}"
                )
    else:
        print(f"Failed to trigger draw: {response.status_code} - {response.text}")


def test_get_daily_winners():
    """Test getting the daily winners"""
    print("\n=== Testing get daily winners ===")

    response = requests.get(f"{BASE_URL}/winners/daily-winners", headers=get_headers())

    if response.status_code == 200:
        winners = response.json()
        print(f"Daily winners (count: {len(winners)}):")
        for winner in winners:
            print(
                f"  {winner['username']} - Position: {winner['position']}, Amount: ${winner['amount_won']}, All-time: ${winner['total_amount_won']}"
            )
    else:
        print(f"Failed to get daily winners: {response.status_code} - {response.text}")


def test_get_weekly_winners():
    """Test getting the weekly winners"""
    print("\n=== Testing get weekly winners ===")

    response = requests.get(f"{BASE_URL}/winners/weekly-winners", headers=get_headers())

    if response.status_code == 200:
        winners = response.json()
        print(f"Weekly winners (count: {len(winners)}):")
        for winner in winners:
            # Use amount_won instead of weekly_amount, but try weekly_amount as fallback
            weekly_amount = winner.get("amount_won", winner.get("weekly_amount", 0))
            print(
                f"  {winner['username']} - Weekly: ${weekly_amount}, All-time: ${winner['total_amount_won']}"
            )
    else:
        print(f"Failed to get weekly winners: {response.status_code} - {response.text}")


def test_get_all_time_winners():
    """Test getting the all-time winners"""
    print("\n=== Testing get all-time winners ===")

    response = requests.get(
        f"{BASE_URL}/winners/all-time-winners", headers=get_headers()
    )

    if response.status_code == 200:
        winners = response.json()
        print(f"All-time winners (count: {len(winners)}):")
        for winner in winners:
            print(f"  {winner['username']} - All-time: ${winner['total_amount_won']}")
    else:
        print(
            f"Failed to get all-time winners: {response.status_code} - {response.text}"
        )


if __name__ == "__main__":
    # Run all tests
    test_get_draw_config()
    test_update_draw_config()
    test_trigger_draw()
    test_get_daily_winners()
    test_get_weekly_winners()
    test_get_all_time_winners()

    print("\nAll tests completed.")
