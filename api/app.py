"""Compatibility entrypoint that exposes the main FastAPI app."""

from main import app as handler

app = handler
