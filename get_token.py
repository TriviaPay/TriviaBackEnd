import json
import sys

import requests


def get_auth0_token(email, password):
    """
    Get an Auth0 access token for the specified email and password.
    """
    print(f"Attempting to get token for: {email}")

    # Auth0 domain and client ID from your Auth0 application settings
    auth0_domain = "triviapay.us.auth0.com"
    client_id = "WsK2Z78mO2sBmEF7NmqmNno5cXZypLbk"

    # Auth0 token endpoint
    url = f"https://{auth0_domain}/oauth/token"

    # Request body
    payload = {
        "grant_type": "password",
        "username": email,
        "password": password,
        "audience": f"https://{auth0_domain}/api/v2/",
        "scope": "openid profile email",
        "client_id": client_id,
    }

    # Make the request
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()

        # Extract token
        token_data = response.json()
        access_token = token_data.get("access_token")

        if access_token:
            print(f"Successfully obtained token for {email}")
            print(f"Access Token: {access_token}")
            return access_token
        else:
            print(f"No access token found in response for {email}")
            print(f"Response: {token_data}")
            return None

    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error for {email}: {str(e)}")
        print(f"Response: {e.response.text}")
        return None
    except Exception as e:
        print(f"Error getting token for {email}: {str(e)}")
        return None


def main():
    if len(sys.argv) < 2:
        print("Usage: python get_token.py <email> <password>")
        print(
            "Or provide multiple accounts: python get_token.py <email1> <password1> <email2> <password2> ..."
        )
        return

    # Check if we have pairs of email/password
    if len(sys.argv) % 2 != 1:
        print("Error: You must provide email and password pairs")
        return

    # Process each email/password pair
    for i in range(1, len(sys.argv), 2):
        email = sys.argv[i]
        password = sys.argv[i + 1]

        token = get_auth0_token(email, password)
        if token:
            print("\nToken can be used in Authorization header as:")
            print(f"Authorization: Bearer {token}\n")
        print("-" * 80)


if __name__ == "__main__":
    main()
