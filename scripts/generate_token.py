import datetime
import os
import sys

import jwt
from dotenv import load_dotenv

# Add the parent directory to the Python path to allow imports from parent
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables
load_dotenv()


def generate_admin_token():
    """
    Generate a JWT token for the admin user with a long expiration time
    """
    # Get environment variables
    auth0_domain = os.getenv("AUTH0_DOMAIN", "triviapay.us.auth0.com")
    auth0_client_secret = os.getenv("AUTH0_CLIENT_SECRET", "")
    api_audience = os.getenv("API_AUDIENCE", "https://triviapay.us.auth0.com/api/v2/")
    admin_email = os.getenv("ADMIN_EMAIL", "triviapay3@gmail.com")

    if not auth0_client_secret:
        print("Error: AUTH0_CLIENT_SECRET not set in environment variables")
        sys.exit(1)

    # Set token expiration to 1 day from now
    iat = datetime.datetime.utcnow()
    exp_time = iat + datetime.timedelta(days=1)

    # Create payload
    payload = {
        "iss": f"https://{auth0_domain}/",
        "sub": f"email|triviapay3_gmail.com",
        "aud": [api_audience, f"https://{auth0_domain}/userinfo"],
        "iat": int(iat.timestamp()),
        "exp": int(exp_time.timestamp()),
        "email": admin_email,
        "email_verified": True,
        "name": "Admin User",
        "azp": "test_client_id",
        "scope": "openid profile email",
    }

    # Generate token
    headers = {"kid": "local_test_key", "typ": "JWT", "alg": "HS256"}
    token = jwt.encode(payload, auth0_client_secret, algorithm="HS256", headers=headers)

    return token


if __name__ == "__main__":
    token = generate_admin_token()
    print("\nAdmin Token (expires in 1 day):")
    print("----------------------------------------------------")
    print(token)
    print("----------------------------------------------------")
    print("\nUse this token for authorization with the header:")
    print("Authorization: Bearer <token>")
    print("\nOr for testing with curl:")
    print(f'curl -H "Authorization: Bearer {token}" ...')
