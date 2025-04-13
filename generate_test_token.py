import jwt
import time
import json
import uuid

def generate_test_token(email="test@example.com", user_id=None):
    """
    Generate a test JWT token that matches Auth0's format for testing purposes.
    
    Args:
        email: The email to include in the token
        user_id: Optional user_id, will be generated if not provided
    
    Returns:
        str: A JWT token
    """
    if not user_id:
        # Generate a unique ID if not provided
        user_id = f"email|{uuid.uuid4().hex[:16]}"
    
    # Current timestamp
    now = int(time.time())
    
    # Token payload that mimics Auth0 tokens
    payload = {
        "iss": "https://triviapay.us.auth0.com/",
        "sub": user_id,
        "aud": [
            "https://triviapay.us.auth0.com/api/v2/",
            "https://triviapay.us.auth0.com/userinfo"
        ],
        "iat": now,
        "exp": now + 86400,  # 24 hours from now
        "scope": "openid profile email read:current_user update:current_user_metadata delete:current_user_metadata create:current_user_metadata create:current_user_device_credentials delete:current_user_device_credentials update:current_user_identities offline_access",
        "gty": "password",
        "azp": "WsK2Z78mO2sBmEF7NmqmNno5cXZypLbk",
        "email": email,
        "email_verified": True,
        "nickname": email.split("@")[0],
        "name": email,
    }
    
    # This is a test secret key - in production, Auth0 uses asymmetric keys
    # Your app should be configured to skip signature verification in development mode
    secret = "your-test-secret-key"
    
    # Generate the token
    token = jwt.encode(payload, secret, algorithm="HS256")
    
    return token

def main():
    # Generate tokens for the specified emails
    emails = ["test@example.com", "krishnatrivia@gmail.com"]
    
    for email in emails:
        token = generate_test_token(email)
        print(f"\nTest token for {email}:")
        print(f"Bearer {token}")
        print("\nToken payload:")
        
        # Decode the token to show the payload (without verification)
        payload = jwt.decode(token, options={"verify_signature": False})
        print(json.dumps(payload, indent=2))
        print("-" * 80)

if __name__ == "__main__":
    main() 