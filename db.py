"""Compatibility shim.

Keep legacy imports like `from db import get_db` working while moving the DB
setup to `core.db`.
"""

from core.db import *  # noqa: F401,F403

