#!/bin/bash

# Ask Sai Baba Backend Docker Build Script

set -e

echo "🐳 Building Ask Sai Baba Backend Docker Image..."

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker and try again."
    exit 1
fi

# Build the image
echo "📦 Building Docker image..."
docker build -t ask-sai-baba-backend .

echo "✅ Build completed successfully!"
echo ""
echo "🚀 To run the container:"
echo "   docker run -d --name ask-sai-baba-backend -p 8000:8000 ask-sai-baba-backend"
echo ""
echo "🔧 Or use docker-compose from the root directory:"
echo "   docker-compose up -d"
echo ""
echo "📋 To view logs:"
echo "   docker logs -f ask-sai-baba-backend"
echo ""
echo "🛑 To stop:"
echo "   docker stop ask-sai-baba-backend" 