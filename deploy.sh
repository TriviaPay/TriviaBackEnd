#!/bin/bash
set -e  # Exit immediately if a command exits with a non-zero status

echo "🚀 Starting deployment process..."

# Show what files have been modified
echo "📋 Modified files:"
git status --porcelain

# Add all files to git
echo "➕ Adding files to git..."
git add .

# Commit changes
echo "💾 Committing changes..."
read -p "Enter commit message: " COMMIT_MESSAGE
git commit -m "$COMMIT_MESSAGE"

# Push to origin
echo "⬆️ Pushing to origin main..."
git push origin main

echo "✅ Deployment process complete!"
echo "🔄 The GitHub Action workflow will automatically trigger the Vercel deployment." 