"""Compatibility shim.

Keep legacy imports like `from auth import validate_descope_jwt` working while moving
authentication/security helpers to `core.security`.
"""

from core.security import *  # noqa: F401,F403

