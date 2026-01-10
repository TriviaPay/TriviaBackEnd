"""
Logging helper utilities for consistent, structured logging across the application.
"""

import logging
from contextvars import ContextVar
from typing import Optional


# Request ID context variable - must match the one in main.py
# We define it here to avoid circular imports, but it should be the same ContextVar instance
# In practice, we'll import it from main when needed, or use a shared module
# Note: request_id_var is defined in main.py and should be imported from there at runtime
# For static analysis, we define a placeholder here
# The actual ContextVar instance is created in main.py and shared via import
def _get_request_id_var():
    """Get the request_id_var from main module."""
    try:
        from main import request_id_var as main_request_id_var

        return main_request_id_var
    except (ImportError, AttributeError):
        # Fallback for when main hasn't loaded
        return ContextVar("request_id", default="")


# Use a lazy getter to avoid circular import issues
request_id_var = None  # Will be set on first use


def get_request_id() -> str:
    """Get the current request ID from context."""
    global request_id_var
    if request_id_var is None:
        request_id_var = _get_request_id_var()
    try:
        return request_id_var.get("")
    except (LookupError, AttributeError):
        return ""


def log_with_context(
    logger: logging.Logger,
    level: int,
    message: str,
    user_id: Optional[int] = None,
    **kwargs,
):
    """
    Log a message with structured context.

    Args:
        logger: The logger instance
        level: Log level (logging.INFO, logging.WARNING, etc.)
        message: The log message
        user_id: Optional user ID for context
        **kwargs: Additional context key-value pairs
    """
    request_id = get_request_id()

    # Build context string
    context_parts = []
    if request_id:
        context_parts.append(f"id={request_id}")
    if user_id:
        context_parts.append(f"user_id={user_id}")

    # Add any additional context
    for key, value in kwargs.items():
        if value is not None:
            context_parts.append(f"{key}={value}")

    context_str = " | ".join(context_parts) if context_parts else ""
    full_message = f"{message} | {context_str}" if context_str else message

    logger.log(level, full_message)


def log_info(
    logger: logging.Logger, message: str, user_id: Optional[int] = None, **kwargs
):
    """Log info message with context."""
    log_with_context(logger, logging.INFO, message, user_id, **kwargs)


def log_warning(
    logger: logging.Logger, message: str, user_id: Optional[int] = None, **kwargs
):
    """Log warning message with context."""
    log_with_context(logger, logging.WARNING, message, user_id, **kwargs)


def log_error(
    logger: logging.Logger,
    message: str,
    user_id: Optional[int] = None,
    exc_info: bool = False,
    **kwargs,
):
    """Log error message with context."""
    request_id = get_request_id()

    context_parts = []
    if request_id:
        context_parts.append(f"id={request_id}")
    if user_id:
        context_parts.append(f"user_id={user_id}")

    for key, value in kwargs.items():
        if value is not None:
            context_parts.append(f"{key}={value}")

    context_str = " | ".join(context_parts) if context_parts else ""
    full_message = f"{message} | {context_str}" if context_str else message

    logger.error(full_message, exc_info=exc_info)


def log_debug(
    logger: logging.Logger, message: str, user_id: Optional[int] = None, **kwargs
):
    """Log debug message with context."""
    log_with_context(logger, logging.DEBUG, message, user_id, **kwargs)
