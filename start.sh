#!/bin/bash
# Start script for Render deployment

# Set environment variables if needed
export PYTHONPATH=.

# Default port
PORT=8000

# Find an available port starting from the default
while [ $(lsof -i:$PORT -t | wc -l) -gt 0 ]; do
    echo "Port $PORT is in use, trying next port..."
    PORT=$((PORT+1))
done

echo "Starting app on port $PORT"

# Start the FastAPI application using uvicorn
exec uvicorn main:app --host 0.0.0.0 --port $PORT 