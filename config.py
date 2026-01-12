"""Compatibility shim.

Keep legacy imports like `from config import ...` working while moving core
configuration to `core.config`.
"""

from core.config import *  # noqa: F401,F403

