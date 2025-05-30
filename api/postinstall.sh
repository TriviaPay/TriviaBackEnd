#!/bin/bash
echo "Starting postinstall script..."
echo "Current directory: $(pwd)"
echo "Current Python version: $(python --version)"
echo "Current pip version: $(pip --version)"

# Clean the environment first
echo "Cleaning pip cache..."
pip cache purge

# Force clean reinstall
echo "Upgrading pip..."
pip install --upgrade pip

echo "Installing requirements with force-reinstall and no-cache..."
pip install --force-reinstall --no-cache-dir -r api/requirements.txt

# Verify installed versions
echo "Installed pydantic version:"
pip show pydantic | grep Version
echo "Installed fastapi version:"
pip show fastapi | grep Version

echo "Postinstall completed successfully!" 