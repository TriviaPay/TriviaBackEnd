#!/bin/bash
set -x  # Print commands for debugging

echo "Starting server via start_server.sh"
echo "Current directory: $(pwd)"
echo "Files in current directory:"
ls -la

# Make sure our WSGI file is available
if [ -f "wsgi.py" ]; then
    echo "wsgi.py found, executing..."
    exec python wsgi.py
else
    echo "ERROR: wsgi.py not found!"
    echo "Attempting to start directly with uvicorn..."
    
    # Export environment variables
    export PYTHONPATH=.
    export PORT="${PORT:-8000}"
    
    echo "Starting app on port $PORT with uvicorn directly"
    exec uvicorn main:app --host 0.0.0.0 --port $PORT --log-level debug
fi 