#!/bin/bash

# Ask Sai Baba Backend - Railway Quick Start Script

echo "🚂 Ask Sai Baba Backend - Railway Quick Start"
echo "=============================================="

# Check if Railway CLI is installed
if ! command -v railway &> /dev/null; then
    echo "❌ Railway CLI not found. Installing..."
    npm install -g @railway/cli
else
    echo "✅ Railway CLI found"
fi

echo ""
echo "🔧 Essential Railway Commands:"
echo "=============================="
echo ""
echo "1. Login to Railway:"
echo "   railway login"
echo ""
echo "2. Initialize project (if first time):"
echo "   railway init"
echo ""
echo "3. Set environment variables:"
echo "   railway variables set MONGO_URI='your_mongodb_connection_string'"
echo "   railway variables set OPENAI_API_KEY='your_openai_api_key'"
echo "   railway variables set FLASK_APP=app.py"
echo "   railway variables set FLASK_ENV=production"
echo "   railway variables set FLASK_DEBUG=0"
echo "   railway variables set FLASK_RUN_PORT=8000"
echo "   railway variables set FLASK_RUN_HOST=0.0.0.0"
echo ""
echo "4. Deploy to Railway:"
echo "   railway up"
echo ""
echo "5. Check status:"
echo "   railway status"
echo ""
echo "6. View logs:"
echo "   railway logs"
echo ""
echo "7. Open deployed app:"
echo "   railway open"
echo ""
echo "📚 For detailed instructions, see: RAILWAY_DEPLOYMENT.md"
echo "🚀 Happy deploying!" 