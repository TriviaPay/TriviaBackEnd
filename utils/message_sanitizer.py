"""
Message sanitization utility to prevent XSS attacks.
Strips HTML tags and escapes special characters from user messages.
"""

import logging

import bleach

from core.config import MESSAGE_SANITIZE_ENABLED

logger = logging.getLogger(__name__)


def sanitize_message(message: str) -> str:
    """
    Sanitize a message by stripping HTML tags and escaping special characters.

    Args:
        message: Raw message string from user

    Returns:
        Sanitized message string safe for display
    """
    if not message:
        return ""

    # Strip whitespace first
    cleaned = message.strip()

    if not MESSAGE_SANITIZE_ENABLED:
        return cleaned

    try:
        # Use bleach to strip all HTML tags and escape special characters
        # tags=[] means no HTML tags are allowed
        # strip=True removes tags instead of escaping them
        sanitized = bleach.clean(cleaned, tags=[], strip=True)

        # Additional safety: remove any remaining control characters
        # Keep only printable characters and common whitespace
        sanitized = "".join(
            char
            for char in sanitized
            if char.isprintable() or char in ["\n", "\r", "\t"]
        )

        return sanitized.strip()
    except Exception as e:
        logger.error(f"Error sanitizing message: {e}")
        # Fallback: return original message stripped (better than failing)
        return cleaned
