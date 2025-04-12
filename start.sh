#!/bin/bash

# Default port
PORT=8000

# Find an available port starting from the default
while [ $(lsof -i:$PORT -t | wc -l) -gt 0 ]; do
    echo "Port $PORT is in use, trying next port..."
    PORT=$((PORT+1))
done

echo "Starting app on port $PORT"
python3 -m uvicorn main:app --reload --port $PORT 