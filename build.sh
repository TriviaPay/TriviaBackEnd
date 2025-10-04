#!/bin/bash

# Ensure the script exits on first error
set -e

echo "Setting up Python environment..."

# Use the Python version specified in environment or default to python3
PYTHON_CMD=${PYTHON_VERSION:-python3}
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# Upgrade pip and install wheel first
$PYTHON_CMD -m pip install --upgrade pip
$PYTHON_CMD -m pip install wheel setuptools

# Install dependencies while ignoring the Vercel package
grep -v "vercel" requirements.txt > temp_requirements.txt
$PYTHON_CMD -m pip install -r temp_requirements.txt

echo "Python environment setup complete!"
echo "Build script finished successfully!" 