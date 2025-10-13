#!/bin/bash

# Ask Sai Baba Backend - Railway Deployment Script
# Complete setup and deployment for Railway

set -e

echo "🚂 Ask Sai Baba Backend - Railway Deployment"
echo "============================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Helper functions
print_status() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Step 1: Check Railway CLI
print_status "Checking Railway CLI installation..."
if ! command -v railway &> /dev/null; then
    print_warning "Railway CLI not found."
    read -p "Install Railway CLI now? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if npm install -g @railway/cli; then
            print_success "Railway CLI installed!"
        else
            print_error "Failed to install Railway CLI. Install manually:"
            echo "   npm install -g @railway/cli"
            exit 1
        fi
    else
        print_error "Railway CLI is required. Exiting."
        exit 1
    fi
else
    print_success "Railway CLI found"
fi

# Step 2: Check login status
print_status "Checking Railway authentication..."
if ! railway whoami &> /dev/null; then
    print_warning "Not logged in to Railway."
    print_status "Railway CLI v4+ uses web-based authentication."
    read -p "Login to Railway now? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if railway login; then
            print_success "Successfully logged in!"
        else
            print_error "Login failed. Please run manually: railway login"
            exit 1
        fi
    else
        print_error "Authentication required. Please run: railway login"
        exit 1
    fi
else
    print_success "Logged in as: $(railway whoami)"
fi

# Step 3: Initialize or verify project
print_status "Checking Railway project..."
if [ ! -f "railway.json" ]; then
    print_status "No Railway project found."
    read -p "Initialize new Railway project? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if railway init; then
            print_success "Railway project initialized!"
        else
            print_error "Failed to initialize project."
            exit 1
        fi
    else
        print_error "Railway project required. Run: railway init"
        exit 1
    fi
else
    print_success "Railway project exists"
fi

# Step 4: Configure environment variables
echo ""
print_status "Environment Variable Configuration"
echo "===================================="
echo ""
echo "Required variables:"
echo "  - MONGO_URI (MongoDB connection string)"
echo "  - OPENAI_API_KEY (OpenAI API key)"
echo "  - WEAVIATE_URL (Weaviate instance URL)"
echo "  - WEAVIATE_API_KEY (Weaviate API key)"
echo ""
echo "Optional variables (auto-configured):"
echo "  - FLASK_APP=app.py"
echo "  - FLASK_ENV=production"
echo "  - FLASK_DEBUG=0"
echo "  - FLASK_RUN_PORT=8000"
echo "  - FLASK_RUN_HOST=0.0.0.0"
echo ""

read -p "Configure environment variables now? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    read -p "📡 MongoDB URI: " MONGO_URI
    read -p "🤖 OpenAI API Key: " OPENAI_API_KEY
    read -p "🔍 Weaviate URL: " WEAVIATE_URL
    read -p "🔑 Weaviate API Key: " WEAVIATE_API_KEY

    print_status "Setting environment variables..."

    # Set required variables
    railway variables set MONGO_URI="$MONGO_URI" && print_success "MONGO_URI set"
    railway variables set OPENAI_API_KEY="$OPENAI_API_KEY" && print_success "OPENAI_API_KEY set"
    railway variables set WEAVIATE_URL="$WEAVIATE_URL" && print_success "WEAVIATE_URL set"
    railway variables set WEAVIATE_API_KEY="$WEAVIATE_API_KEY" && print_success "WEAVIATE_API_KEY set"

    # Set Flask configuration
    railway variables set FLASK_APP=app.py
    railway variables set FLASK_ENV=production
    railway variables set FLASK_DEBUG=0
    railway variables set FLASK_RUN_PORT=8000
    railway variables set FLASK_RUN_HOST=0.0.0.0

    print_success "All environment variables configured!"
else
    print_warning "Skipping environment variable setup."
    echo ""
    echo "Set variables manually with:"
    echo "  railway variables set MONGO_URI='your_mongodb_uri'"
    echo "  railway variables set OPENAI_API_KEY='your_openai_key'"
    echo "  railway variables set WEAVIATE_URL='your_weaviate_url'"
    echo "  railway variables set WEAVIATE_API_KEY='your_weaviate_key'"
    echo "  railway variables set FLASK_APP=app.py"
    echo "  railway variables set FLASK_ENV=production"
    echo "  railway variables set FLASK_DEBUG=0"
    echo "  railway variables set FLASK_RUN_PORT=8000"
    echo "  railway variables set FLASK_RUN_HOST=0.0.0.0"
    echo ""
fi

# Step 5: Deploy
echo ""
print_status "Ready to deploy!"
echo ""
echo "📋 Quick Commands:"
echo "  Deploy:       railway up"
echo "  Status:       railway status"
echo "  Logs:         railway logs"
echo "  Open app:     railway open"
echo "  Variables:    railway variables"
echo ""

read -p "🚀 Deploy to Railway now? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    print_status "Deploying to Railway..."
    if railway up; then
        echo ""
        print_success "🎉 Deployment successful!"
        echo ""
        print_status "View your deployment:"
        echo "  🌐 Open app:  railway open"
        echo "  📊 Status:    railway status"
        echo "  📝 Logs:      railway logs"
    else
        print_error "Deployment failed. Check logs above."
        echo "Retry with: railway up"
        exit 1
    fi
else
    print_status "Deploy when ready with: railway up"
fi

echo ""
print_success "🚂 Railway deployment script complete!"
echo "📚 For more info, see: RAILWAY_DEPLOYMENT.md"
