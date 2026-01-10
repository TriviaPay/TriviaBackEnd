import json
import os
import sys
from http.server import BaseHTTPRequestHandler


# This is a simple HTTP handler for Vercel
class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def respond(self):
        # Special handling for docs endpoint - redirect to static Swagger UI
        if self.path == "/docs" or self.path.startswith("/docs#"):
            self.send_response(302)  # Redirect
            self.send_header("Location", "/swagger")
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        # Special handling for OpenAPI specification
        if self.path == "/openapi.json":
            # Define OpenAPI spec in JSON format
            openapi_spec = {
                "openapi": "3.0.2",
                "info": {
                    "title": "TriviaPay API",
                    "version": "1.0.0",
                    "description": "Backend API for TriviaPay application",
                },
                "paths": {
                    "/": {
                        "get": {
                            "summary": "Root endpoint",
                            "responses": {
                                "200": {"description": "Successful response"}
                            },
                        }
                    },
                    "/health": {
                        "get": {
                            "summary": "Health check endpoint",
                            "responses": {
                                "200": {"description": "Successful response"}
                            },
                        }
                    },
                },
            }
            self.wfile.write(json.dumps(openapi_spec).encode())
            return

        # Regular API response
        message = {
            "status": "online",
            "message": "TriviaPay API is working!",
            "path": self.path,
            "python_version": sys.version,
            "endpoints": [
                {
                    "path": "/",
                    "method": "GET",
                    "description": "Root endpoint to check if the API is running",
                },
                {
                    "path": "/health",
                    "method": "GET",
                    "description": "Health check endpoint",
                },
                {
                    "path": "/openapi.json",
                    "method": "GET",
                    "description": "OpenAPI specification",
                },
                {
                    "path": "/swagger",
                    "method": "GET",
                    "description": "Swagger UI documentation",
                },
            ],
        }

        self.wfile.write(json.dumps(message).encode())
