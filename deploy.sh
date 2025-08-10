#!/bin/bash

# Ask Sai Baba Backend Deployment Script

set -e

echo "🚀 Deploying Ask Sai Baba Backend..."

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker and try again."
    exit 1
fi

# Check if .env file exists
if [ ! -f .env ]; then
    echo "⚠️  Warning: .env file not found. Creating from production.env..."
    if [ -f backend/production.env ]; then
        cp backend/production.env .env
        echo "✅ Created .env file from production.env"
        echo "📝 Please edit .env file with your actual configuration values"
        echo "   - MONGO_URI: Your MongoDB connection string"
        echo "   - OPENAI_API_KEY: Your OpenAI API key"
        read -p "Press Enter after updating .env file to continue..."
    else
        echo "❌ production.env file not found. Please create .env file manually."
        exit 1
    fi
fi

# Stop existing containers if running
echo "🛑 Stopping existing containers..."
docker-compose down 2>/dev/null || true

# Build and start the services
echo "🔨 Building and starting services..."
docker-compose up --build -d

# Wait for service to be ready
echo "⏳ Waiting for service to be ready..."
sleep 10

# Check if service is running
if curl -f http://localhost:8000/ > /dev/null 2>&1; then
    echo "✅ Backend is running successfully!"
    echo "🌐 Access your application at: http://localhost:8000"
    echo ""
    echo "📋 Useful commands:"
    echo "   View logs: docker-compose logs -f backend"
    echo "   Stop: docker-compose down"
    echo "   Restart: docker-compose restart backend"
else
    echo "❌ Service is not responding. Check logs with: docker-compose logs backend"
    exit 1
fi 