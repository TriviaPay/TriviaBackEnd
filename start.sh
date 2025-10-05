#!/bin/bash
# Start script for deployment

# Set environment variables
export PYTHONPATH=.

# Use PORT environment variable or default to 8000
PORT="${PORT:-8000}"

echo "Starting app on port $PORT"

# Start the FastAPI application using uvicorn
exec uvicorn main:app --host 0.0.0.0 --port $PORT 