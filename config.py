import os
from dotenv import load_dotenv

load_dotenv()  # Loads variables from .env file if present

AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "")
AUTH0_CLIENT_ID = os.getenv("AUTH0_CLIENT_ID", "")
AUTH0_CLIENT_SECRET = os.getenv("AUTH0_CLIENT_SECRET", "")
AUTH0_CONNECTION = os.getenv("AUTH0_CONNECTION", "email")  # "email" for passwordless
API_AUDIENCE = os.getenv("API_AUDIENCE", "")               # If using an Auth0 API
AUTH0_ALGORITHMS = ["RS256"]

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/mydb")

# If you want to store/verify tokens with a particular issuer:
AUTH0_ISSUER = f"https://{AUTH0_DOMAIN}/" if AUTH0_DOMAIN else ""
