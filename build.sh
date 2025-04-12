#!/bin/bash

# Ensure the script exits on first error
set -e

echo "Setting up Python environment..."

# Use Python 3.9 explicitly
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# Upgrade pip and install wheel first
python3.9 -m pip install --upgrade pip
python3.9 -m pip install wheel setuptools

# Install dependencies while ignoring the Vercel package
grep -v "vercel" requirements.txt > temp_requirements.txt
python3.9 -m pip install -r temp_requirements.txt

echo "Python environment setup complete!"
echo "Build script finished successfully!" 