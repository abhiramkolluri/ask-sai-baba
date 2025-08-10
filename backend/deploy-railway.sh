#!/bin/bash

# Ask Sai Baba Backend Railway Deployment Script

set -e

echo "🚂 Preparing Ask Sai Baba Backend for Railway Deployment..."

# Check if Railway CLI is installed
if ! command -v railway &> /dev/null; then
    echo "❌ Railway CLI is not installed."
    echo "📥 Please install it first:"
    echo "   npm install -g @railway/cli"
    echo "   or visit: https://railway.app/docs/develop/cli"
    exit 1
fi

# Check if user is logged in to Railway
if ! railway whoami &> /dev/null; then
    echo "🔐 Please login to Railway first:"
    echo "   railway login"
    exit 1
fi

echo "✅ Railway CLI is ready"

# Check if we're in a Railway project
if [ ! -f "railway.json" ]; then
    echo "📁 Creating Railway project..."
    railway init
else
    echo "📁 Railway project already exists"
fi

echo ""
echo "🔧 Next steps for deployment:"
echo "1. Configure environment variables:"
echo "   railway variables set MONGO_URI='your_mongodb_connection_string'"
echo "   railway variables set OPENAI_API_KEY='your_openai_api_key'"
echo "   railway variables set FLASK_APP=app.py"
echo "   railway variables set FLASK_ENV=production"
echo "   railway variables set FLASK_DEBUG=0"
echo "   railway variables set FLASK_RUN_PORT=8000"
echo "   railway variables set FLASK_RUN_HOST=0.0.0.0"
echo ""
echo "2. Deploy to Railway:"
echo "   railway up"
echo ""
echo "3. View deployment status:"
echo "   railway status"
echo ""
echo "4. View logs:"
echo "   railway logs"
echo ""
echo "5. Open the deployed app:"
echo "   railway open"
echo ""
echo "🚀 Your backend will be deployed to Railway!"

# Optional: Auto-deploy if requested
read -p "🤔 Would you like to deploy now? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "🚂 Deploying to Railway..."
    railway up
    echo "✅ Deployment complete!"
    echo "🌐 Your app is now live on Railway!"
else
    echo "📝 Ready to deploy when you're ready!"
fi 