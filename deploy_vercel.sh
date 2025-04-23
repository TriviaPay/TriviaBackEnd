#!/bin/bash
set -e  # Exit immediately if a command exits with a non-zero status

echo "🚀 Starting Vercel deployment process..."

# Show what files have been modified
echo "📋 Modified files:"
git status --porcelain

# Add all files to git
echo "➕ Adding files to git..."
git add .

# Commit changes
echo "💾 Committing changes..."
git commit -m "Fix Vercel deployment: Simplify ASGI handler and use FastAPI 0.109.2 with Pydantic 2.6.4"

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