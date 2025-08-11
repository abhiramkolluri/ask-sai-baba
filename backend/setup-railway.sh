#!/bin/bash

# Ask Sai Baba Backend - Complete Railway Setup Script

set -e

echo "🚂 Ask Sai Baba Backend - Complete Railway Setup"
echo "================================================"

# Railway API Key
RAILWAY_TOKEN="ff788cb9-5ed4-4ee4-9567-540cd32e922a"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Step 1: Install Railway CLI
print_status "Step 1: Installing Railway CLI..."
if ! command -v railway &> /dev/null; then
    print_status "Railway CLI not found. Installing..."
    if npm install -g @railway/cli; then
        print_success "Railway CLI installed successfully!"
    else
        print_error "Failed to install Railway CLI. Please install manually:"
        echo "   npm install -g @railway/cli"
        exit 1
    fi
else
    print_success "Railway CLI already installed"
fi

# Step 2: Login to Railway
print_status "Step 2: Logging in to Railway..."
print_status "Railway CLI v4+ requires web-based authentication."
print_status "You'll need to complete the login process in your browser."

# Try to login
if railway login; then
    print_success "Successfully logged in to Railway!"
    if railway whoami &> /dev/null; then
        print_status "User: $(railway whoami)"
    fi
else
    print_warning "Login process may need to be completed manually."
    print_status "Please run: railway login"
    print_status "Then continue with this script."
fi

# Step 3: Initialize Railway Project
print_status "Step 3: Initializing Railway project..."
if [ ! -f "railway.json" ]; then
    print_status "Creating new Railway project..."
    if railway init; then
        print_success "Railway project initialized!"
    else
        print_warning "Failed to initialize project. You may need to do this manually."
        print_status "Run: railway init"
    fi
else
    print_success "Railway project already exists"
fi

# Step 4: Set Environment Variables
print_status "Step 4: Setting up environment variables..."
print_status "You'll need to set these environment variables in Railway:"
echo ""
echo "Required Variables:"
echo "=================="
echo "MONGO_URI - Your MongoDB connection string"
echo "OPENAI_API_KEY - Your OpenAI API key"
echo "FLASK_APP=app.py"
echo "FLASK_ENV=production"
echo "FLASK_DEBUG=0"
echo "FLASK_RUN_PORT=8000"
echo "FLASK_RUN_HOST=0.0.0.0"
echo ""

# Ask user if they want to set variables now
read -p "🤔 Do you have your MongoDB URI and OpenAI API key ready? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    read -p "📡 Enter your MongoDB URI: " MONGO_URI
    read -p "🤖 Enter your OpenAI API key: " OPENAI_API_KEY
    
    print_status "Setting environment variables in Railway..."
    
    # Set the variables
    if railway variables set MONGO_URI="$MONGO_URI"; then
        print_success "MONGO_URI set successfully!"
    else
        print_warning "Failed to set MONGO_URI. You may need to set it manually."
    fi
    
    if railway variables set OPENAI_API_KEY="$OPENAI_API_KEY"; then
        print_success "OPENAI_API_KEY set successfully!"
    else
        print_warning "Failed to set OPENAI_API_KEY. You may need to set it manually."
    fi
    
    # Set Flask variables
    railway variables set FLASK_APP=app.py
    railway variables set FLASK_ENV=production
    railway variables set FLASK_DEBUG=0
    railway variables set FLASK_RUN_PORT=8000
    railway variables set FLASK_RUN_HOST=0.0.0.0
    
    print_success "All environment variables set!"
else
    print_status "You can set environment variables later using:"
    echo "   railway variables set MONGO_URI='your_mongodb_connection_string'"
    echo "   railway variables set OPENAI_API_KEY='your_openai_api_key'"
    echo "   railway variables set FLASK_APP=app.py"
    echo "   railway variables set FLASK_ENV=production"
    echo "   railway variables set FLASK_DEBUG=0"
    echo "   railway variables set FLASK_RUN_PORT=8000"
    echo "   railway variables set FLASK_RUN_HOST=0.0.0.0"
fi

# Step 5: Ready to Deploy
echo ""
print_success "🎉 Railway setup complete!"
echo ""
echo "🚀 Next steps:"
echo "=============="
echo "1. Deploy your backend: railway up"
echo "2. Check deployment status: railway status"
echo "3. View logs: railway logs"
echo "4. Open deployed app: railway open"
echo ""
echo "📚 For detailed instructions, see: RAILWAY_DEPLOYMENT.md"
echo "🔧 For quick reference, see: quick-start-railway.sh"
echo ""

# Ask if user wants to deploy now
read -p "🤔 Would you like to deploy now? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    print_status "🚂 Deploying to Railway..."
    if railway up; then
        print_success "🎉 Deployment successful!"
        print_status "Your app is now live on Railway!"
        echo ""
        print_status "To view your app: railway open"
        print_status "To check status: railway status"
        print_status "To view logs: railway logs"
    else
        print_error "❌ Deployment failed. Check the logs above for errors."
        print_status "You can try deploying again with: railway up"
    fi
else
    print_status "📝 Ready to deploy when you're ready!"
    print_status "Run: railway up"
fi

echo ""
print_success "🚂 Happy deploying on Railway!" 