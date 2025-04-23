#!/bin/bash
set -e  # Exit immediately if a command exits with a non-zero status

echo "Installing Python dependencies..."
pip install -r requirements.txt --no-cache-dir

echo "Build completed successfully!" 