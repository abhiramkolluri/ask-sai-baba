# Docker Deployment Guide for Ask Sai Baba Backend

This guide explains how to deploy the Ask Sai Baba backend using Docker.

## Prerequisites

- Docker installed on your system
- Docker Compose installed
- MongoDB instance running (local or remote)
- OpenAI API key

## Quick Start

### 1. Build and Run with Docker Compose

```bash
# Navigate to the project root directory
cd /path/to/ask-sai-baba

# Build and start the backend
docker-compose up --build

# Or run in detached mode
docker-compose up -d --build
```

### 2. Build and Run with Docker Only

```bash
# Navigate to the backend directory
cd backend

# Build the Docker image
docker build -t ask-sai-baba-backend .

# Run the container
docker run -d \
  --name ask-sai-baba-backend \
  -p 8000:8000 \
  -e MONGO_URI="your_mongodb_connection_string" \
  -e OPENAI_API_KEY="your_openai_api_key" \
  ask-sai-baba-backend
```

## Environment Variables

Create a `.env` file in the project root with the following variables:

```bash
# MongoDB Connection
MONGO_URI=mongodb://localhost:27017/saibabasayings

# OpenAI Configuration
OPENAI_API_KEY=your_openai_api_key_here
```

## Docker Commands

### View running containers
```bash
docker ps
```

### View logs
```bash
# Using docker-compose
docker-compose logs -f backend

# Using docker
docker logs -f ask-sai-baba-backend
```

### Stop the service
```bash
# Using docker-compose
docker-compose down

# Using docker
docker stop ask-sai-baba-backend
docker rm ask-sai-baba-backend
```

### Restart the service
```bash
# Using docker-compose
docker-compose restart backend

# Using docker
docker restart ask-sai-baba-backend
```

## Health Checks

The container includes health checks that verify the application is running:

```bash
# Check container health
docker inspect ask-sai-baba-backend | grep Health -A 10
```

## Volumes

The application logs are persisted to `./backend/logs` on the host machine.

## Networking

The container runs on port 8000 and is accessible at `http://localhost:8000`.

## Troubleshooting

### Common Issues

1. **Port already in use**: Ensure port 8000 is not occupied by another service
2. **MongoDB connection failed**: Verify your MongoDB URI and network connectivity
3. **OpenAI API errors**: Check your API key and billing status

### Debug Mode

To run in debug mode, modify the Dockerfile:

```dockerfile
ENV FLASK_DEBUG=1
ENV FLASK_ENV=development
```

### View container details
```bash
docker inspect ask-sai-baba-backend
```

### Access container shell
```bash
docker exec -it ask-sai-baba-backend /bin/bash
```

## Production Considerations

1. **Security**: The container runs as a non-root user
2. **Resource limits**: Consider adding memory and CPU limits
3. **Logging**: Logs are persisted to host volumes
4. **Restart policy**: Container automatically restarts unless manually stopped

## Scaling

To scale the backend service:

```bash
docker-compose up --scale backend=3
```

## Monitoring

Monitor the application using:

```bash
# Resource usage
docker stats ask-sai-baba-backend

# Logs
docker logs -f ask-sai-baba-backend

# Health status
docker inspect ask-sai-baba-backend | grep Health
``` 