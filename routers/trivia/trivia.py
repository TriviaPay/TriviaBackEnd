"""
Legacy compatibility module.

`routers/trivia/api.py` no longer mounts a `/trivia` router from here. This file exists
only to preserve older imports of `get_active_draw_date`.
"""

from .service import get_active_draw_date

__all__ = ["get_active_draw_date"]
