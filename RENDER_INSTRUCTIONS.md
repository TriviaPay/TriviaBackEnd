# Render Deployment Instructions

If you need to manually configure the deployment in the Render dashboard, use the following settings:

## Build Command
```bash
pip install -r requirements.txt && chmod +x start_server.sh
```

## Start Command
```bash
bash start_server.sh
```

This configuration ensures that:
1. All dependencies are installed
2. The start_server.sh script is executable
3. The script runs your FastAPI application with proper port binding

## Environment Variables
Make sure to set these environment variables in your Render dashboard:
- `PYTHON_VERSION`: 3.11.7
- `PORT`: 8000

## Important Notes
- The `start_server.sh` script will check for `wsgi.py` and run it if available
- If wsgi.py is not found, it will fall back to running uvicorn directly
- The script explicitly binds to all network interfaces (0.0.0.0) to ensure Render can detect the open port 