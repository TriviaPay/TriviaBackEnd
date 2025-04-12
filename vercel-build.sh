#!/bin/bash

# Upgrade pip and install build tools
pip install --upgrade pip
pip install wheel setuptools

# Install requirements
if [ -f requirements.txt ]; then
    pip install -r requirements.txt
fi

echo "Build completed successfully" 