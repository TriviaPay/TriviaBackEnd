from http.server import BaseHTTPRequestHandler
import json
import sys

# This is a simple HTTP handler for Vercel
class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.respond()
    
    def do_POST(self):
        self.respond()
        
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def respond(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        # Special handling for docs endpoint
        if self.path == '/docs' or self.path.startswith('/docs#'):
            message = {
                "status": "unavailable", 
                "message": "Swagger UI is not available in this deployment. Please use the API endpoints directly.",
                "info": "The Swagger UI cannot be served through Vercel's serverless handler. Contact the team for API documentation.",
                "path": self.path,
                "python_version": sys.version
            }
        else:
            message = {
                "status": "online",
                "message": "TriviaPay API is working!",
                "path": self.path,
                "python_version": sys.version,
                "endpoints": [
                    {"path": "/", "method": "GET", "description": "Root endpoint to check if the API is running"},
                    {"path": "/health", "method": "GET", "description": "Health check endpoint"},
                    {"path": "/api/info", "method": "GET", "description": "API information endpoint"}
                ]
            }
        
        self.wfile.write(json.dumps(message).encode()) 