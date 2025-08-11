#!/bin/bash

# Ask Sai Baba Backend - Railway Login Script

echo "🚂 Ask Sai Baba Backend - Railway Login"
echo "========================================"

# Check if Railway CLI is installed
if ! command -v railway &> /dev/null; then
    echo "❌ Railway CLI not found. Installing..."
    npm install -g @railway/cli
else
    echo "✅ Railway CLI found"
fi

echo ""
echo "🔐 Logging in to Railway using API key..."

# Login using the API key
RAILWAY_TOKEN="ff788cb9-5ed4-4ee4-9567-540cd32e922a"

# Set the token as an environment variable
export RAILWAY_TOKEN="$RAILWAY_TOKEN"

echo "✅ Railway API key configured"
echo "🔑 Token: ${RAILWAY_TOKEN:0:8}...${RAILWAY_TOKEN: -4}"

# Verify login
echo ""
echo "🔍 Verifying Railway connection..."
if railway whoami &> /dev/null; then
    echo "✅ Successfully logged in to Railway!"
    echo "👤 User: $(railway whoami)"
else
    echo "❌ Failed to login. Please check your API key."
    exit 1
fi

echo ""
echo "🚀 Ready to deploy! Next steps:"
echo "1. Initialize Railway project: railway init"
echo "2. Set environment variables: railway variables set KEY=value"
echo "3. Deploy: railway up"
echo ""
echo "📚 For detailed instructions, see: RAILWAY_DEPLOYMENT.md"
echo "🚂 Happy deploying!" 