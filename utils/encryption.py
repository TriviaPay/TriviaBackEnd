import base64
import os

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Get the encryption key from environment or create one
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    # This is just for development; in production, the key should be set in environment
    salt = os.urandom(16)
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    ENCRYPTION_KEY = base64.urlsafe_b64encode(kdf.derive(b"triviapay-default-key"))

# Initialize Fernet with the encryption key
fernet = Fernet(ENCRYPTION_KEY)


def encrypt_data(data: str) -> str:
    """
    Encrypt sensitive data using Fernet symmetric encryption

    Args:
        data (str): The sensitive data to encrypt

    Returns:
        str: The encrypted data as a base64-encoded string
    """
    if not data:
        return None

    encrypted = fernet.encrypt(data.encode())
    return encrypted.decode()


def decrypt_data(encrypted_data: str) -> str:
    """
    Decrypt sensitive data that was encrypted with Fernet

    Args:
        encrypted_data (str): The encrypted data as a base64-encoded string

    Returns:
        str: The decrypted data
    """
    if not encrypted_data:
        return None

    decrypted = fernet.decrypt(encrypted_data.encode())
    return decrypted.decode()


def mask_account_number(account_number: str) -> str:
    """
    Mask an account number showing only the last 4 digits

    Args:
        account_number (str): The full account number

    Returns:
        str: The masked account number (e.g., "****6789")
    """
    if not account_number or len(account_number) < 4:
        return "****"

    return "*" * (len(account_number) - 4) + account_number[-4:]


def get_last_four(account_number: str) -> str:
    """
    Get the last 4 digits of an account number

    Args:
        account_number (str): The full account number

    Returns:
        str: The last 4 digits
    """
    if not account_number or len(account_number) < 4:
        return "0000"

    return account_number[-4:]
