#!/bin/bash
set -e  # Exit immediately if a command exits with a non-zero status

echo "🚀 Starting Vercel deployment process..."

# Make sure alembic directory exists
if [ ! -d "alembic/versions" ]; then
    echo "Creating alembic directory structure..."
    mkdir -p alembic/versions
    touch alembic/versions/init_boost_config.py
    echo "# Initial migration file - placeholder" > alembic/versions/init_boost_config.py
fi

# Show what files have been modified
echo "📋 Modified files:"
git status --porcelain

# Add all files to git
echo "➕ Adding files to git..."
git add .

# Commit changes
echo "💾 Committing changes..."
git commit -m "Fix Vercel handler: Use BaseHTTPRequestHandler class for compatibility"

# Push to origin
echo "⬆️ Pushing to origin..."
git push origin main

# Deploy to Vercel (optional - if you have Vercel CLI installed)
if command -v vercel &> /dev/null; then
    echo "🚀 Deploying to Vercel..."
    vercel --prod --force
else
    echo "⚠️ Vercel CLI not found. Skipping direct deployment."
    echo "Please deploy manually via Vercel dashboard or install Vercel CLI and run:"
    echo "vercel --prod --force"
fi

echo "✅ Deployment process complete!" 