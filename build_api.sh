#!/bin/bash
set -e  # Exit immediately if a command exits with a non-zero status

# Create api directory if it doesn't exist
mkdir -p api

# Copy requirements to api directory
cp requirements.txt api/requirements.txt

# Install requirements in a clean environment
cd api
pip install -r requirements.txt

echo "API build completed successfully!" 