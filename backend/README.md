# Ask Sai Baba Backend

AI-powered search and query system for Sai Baba discourses using Weaviate hybrid search, OpenAI, and MongoDB.

## 📋 Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Application](#running-the-application)
- [API Endpoints](#api-endpoints)
- [Deployment](#deployment)
- [Testing](#testing)
- [Architecture](#architecture)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)

## Overview

Ask Sai Baba is a backend service that provides intelligent search and conversational query capabilities over a collection of Sai Baba discourses. It uses Weaviate's hybrid search (combining semantic vector search and keyword search) to find relevant content and OpenAI's language models to generate contextual responses.

## Features

- 🔍 **Hybrid Search**: Combines semantic vector search (75%) with keyword search (25%) using Weaviate
- 🤖 **AI-Powered Responses**: Generates contextual answers using OpenAI GPT models
- 💾 **Conversation Memory**: Stores chat history in MongoDB with user authentication
- 🔐 **Auth0 Integration**: Secure authentication with Auth0 JWT tokens
- 🚀 **Production Ready**: Docker support with health checks and environment validation
- 📊 **Monitoring**: Built-in health checks and logging
- 🌐 **CORS Support**: Configured for frontend integration

## Tech Stack

- **Framework**: Flask (Python)
- **Vector Database**: Weaviate (hybrid search)
- **Database**: MongoDB (user data, conversations)
- **AI/ML**: OpenAI (embeddings & chat), LangChain
- **Authentication**: Auth0 (JWT tokens)
- **Deployment**: Docker, Railway, Railway CLI
- **Testing**: Postman, cURL

## Quick Start

### Prerequisites

- Python 3.9+
- MongoDB instance
- Weaviate instance (local or cloud)
- OpenAI API key
- Docker (optional, for containerized deployment)

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd ask-sai-baba/backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create .env file
cp .env.example .env
# Edit .env with your configuration
```

### Configuration

Create a `.env` file in the `backend` directory:

```bash
# MongoDB
MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net/saibabasayings

# OpenAI
OPENAI_API_KEY=sk-your-openai-api-key

# Weaviate
WEAVIATE_URL=https://your-cluster.weaviate.cloud
WEAVIATE_API_KEY=your-weaviate-api-key

# Flask
FLASK_APP=app.py
FLASK_ENV=production
FLASK_DEBUG=0
FLASK_RUN_PORT=8000
FLASK_RUN_HOST=0.0.0.0
```

**Alternative**: Create `openai.ini` for OpenAI key only:
```ini
[OpenAI]
api_key=sk-your-openai-api-key
```

### Running the Application

#### Option 1: Local Development

```bash
# Activate virtual environment
source venv/bin/activate

# Run the application
python app.py
```

The server will start at `http://localhost:8000`

#### Option 2: Docker Compose (Recommended)

```bash
# From project root
docker-compose up -d

# View logs
docker-compose logs -f backend

# Stop services
docker-compose down
```

#### Option 3: Docker Only

```bash
# Build and run
docker build -t ask-sai-baba-backend .
docker run -d \
  --name ask-sai-baba-backend \
  -p 8000:8000 \
  --env-file .env \
  ask-sai-baba-backend
```

### Validate Environment

Before running, validate your configuration:

```bash
python validate-env.py
```

This checks all required environment variables and provides helpful error messages.

## API Endpoints

### Health Check

```bash
GET /
```

Returns service status and health information.

**Response:**
```json
{
  "status": "healthy",
  "service": "ask-sai-baba-backend",
  "version": "1.0.0",
  "vector_store_healthy": true
}
```

### Search

```bash
POST /search
Content-Type: application/json

{
  "query": "What is the purpose of life?"
}
```

Returns search results using Weaviate hybrid search.

**Response:**
```json
[
  {
    "_id": "discourse-id",
    "title": "Discourse Title",
    "content": "Discourse content...",
    "collection": "SSS, Vol 1",
    "location": "Prasanthi Nilayam",
    "link": "https://...",
    "score": 0.85
  }
]
```

### Get Article

```bash
GET /blog/<id>
```

Retrieves full article content by ID.

### Chat Management (Authenticated)

All chat endpoints require Auth0 authentication via `Authorization: Bearer <token>` header.

#### Get User Chats

```bash
GET /chats/<user_email>
Authorization: Bearer <token>
```

#### Create Chat Thread

```bash
POST /chats/<user_email>
Authorization: Bearer <token>
Content-Type: application/json

{
  "user_email": "user@example.com",
  "title": "My Conversation"
}
```

#### Update Chat Thread

```bash
PUT /chats/<thread_id>
Authorization: Bearer <token>
Content-Type: application/json

{
  "user_email": "user@example.com",
  "title": "Updated Title",
  "messages": [...]
}
```

#### Delete Chat Thread

```bash
DELETE /chats/<thread_id>
Authorization: Bearer <token>
Content-Type: application/json

{
  "user_email": "user@example.com"
}
```

### Conversation Memory

#### Clear Conversation

```bash
POST /conversation/clear
Content-Type: application/json

{
  "session_id": "session-123",
  "user_id": "user-456"
}
```

#### Get Conversation History

```bash
GET /conversation/history?session_id=session-123&user_id=user-456
```

## Testing

### Using cURL

```bash
# Health check
curl http://localhost:8000/

# Search
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the purpose of meditation?"}'
```

### Using Postman

1. Open Postman
2. Create a new POST request to `http://localhost:8000/search`
3. Set Headers: `Content-Type: application/json`
4. Body (raw JSON):
   ```json
   {
     "query": "What is devotion?"
   }
   ```
5. Click Send

## Deployment

### Docker Deployment

#### Prerequisites

- Docker installed on your system
- Docker Compose installed (for full stack deployment)

#### Quick Start with Docker Compose (Recommended)

Run both backend and MongoDB together:

```bash
# Navigate to project root (parent of backend/)
cd /path/to/ask-sai-baba

# Start all services
docker-compose up -d

# View logs
docker-compose logs -f backend

# Stop services
docker-compose down
```

#### Docker Only (Backend Container)

Run just the backend container:

```bash
# Navigate to backend directory
cd backend

# Validate environment before building
python validate-env.py

# Build and run with environment variables
docker build -t ask-sai-baba-backend .
docker run -d \
  --name ask-sai-baba-backend \
  -p 8000:8000 \
  --env-file .env \
  ask-sai-baba-backend
```

#### Docker Commands Reference

**Building and Running:**
```bash
# Build the image
docker build -t ask-sai-baba-backend .

# Run with docker-compose (recommended)
docker-compose up -d

# Run standalone container
docker run -d --name backend -p 8000:8000 --env-file .env ask-sai-baba-backend

# Run in foreground (for debugging)
docker-compose up
```

**Viewing Logs:**
```bash
# Using docker-compose
docker-compose logs -f backend

# Using docker
docker logs -f ask-sai-baba-backend

# View last 100 lines
docker logs --tail 100 ask-sai-baba-backend
```

**Managing Containers:**
```bash
# View running containers
docker ps

# View all containers
docker ps -a

# Stop services
docker-compose down                    # Using compose
docker stop ask-sai-baba-backend      # Using docker

# Restart services
docker-compose restart backend         # Using compose
docker restart ask-sai-baba-backend   # Using docker

# Remove containers
docker rm ask-sai-baba-backend
docker rmi ask-sai-baba-backend

# Clean up all stopped containers
docker container prune
```

**Health Checks:**
```bash
# Check container health
docker inspect ask-sai-baba-backend | grep Health -A 10

# Test the health endpoint
curl http://localhost:8000/
```

**Debugging:**
```bash
# Access container shell
docker exec -it ask-sai-baba-backend /bin/bash

# View container details
docker inspect ask-sai-baba-backend

# Monitor resource usage
docker stats ask-sai-baba-backend
```

#### Network Configuration

The application uses Docker networking:

```yaml
# In docker-compose.yml
networks:
  ask-sai-baba-network:
    driver: bridge
```

Backend is accessible at:
- **Inside Docker network**: `http://backend:8000`
- **From host machine**: `http://localhost:8000`

#### Volumes and Data Persistence

Persistent data volumes:

```yaml
volumes:
  mongodb_data:              # MongoDB data persistence
  ./backend/logs:/app/logs   # Application logs
```

Logs are stored in `./backend/logs` on your host machine.

#### Troubleshooting Docker Deployment

**Docker daemon not running:**
```bash
# Start Docker Desktop (macOS/Windows)
# Or start service (Linux)
sudo systemctl start docker
```

**Port 8000 already in use:**
```bash
# Find process using port 8000
lsof -i :8000

# Kill the process
kill -9 <PID>

# Or change the port in docker-compose.yml
ports:
  - "8001:8000"  # Map to different host port
```

**Build failures:**
```bash
# Check Dockerfile syntax
# Verify requirements.txt exists
docker-compose build --no-cache

# View build logs
docker-compose logs backend
```

**Container exits immediately:**
```bash
# Check logs for errors
docker logs ask-sai-baba-backend

# Run in foreground to see errors
docker-compose up

# Verify environment variables
docker exec ask-sai-baba-backend env
```

**MongoDB connection issues:**
```bash
# Ensure MongoDB is running
docker-compose ps

# Check MongoDB logs
docker-compose logs mongodb

# Verify MONGO_URI in .env
cat .env | grep MONGO_URI
```

#### Production Considerations

**Security:**
- Container runs as non-root user
- Never commit `.env` to version control
- Use secrets management in production
- Update CORS origins in `app.py` for production domains

**Resource Limits:**

Add to `docker-compose.yml`:
```yaml
services:
  backend:
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 512M
        reservations:
          cpus: '0.5'
          memory: 256M
```

**Logging:**
- Logs persisted to `./backend/logs` volume
- Configure log rotation for production
- Use centralized logging (e.g., ELK stack) in production

**Health Checks:**
```yaml
# In docker-compose.yml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 40s
```

**Restart Policy:**
```yaml
# In docker-compose.yml
restart: unless-stopped
```

#### Scaling with Docker

Run multiple backend instances:

```bash
# Scale to 3 instances
docker-compose up --scale backend=3

# Note: Requires load balancer in front
```

#### Monitoring

**Health Monitoring:**
```bash
# Check health status
docker inspect ask-sai-baba-backend | grep Health

# Continuous health check
watch -n 5 'curl -s http://localhost:8000/ | jq'
```

**Performance Monitoring:**
```bash
# Real-time resource usage
docker stats ask-sai-baba-backend

# Container logs
docker logs -f --tail 100 ask-sai-baba-backend
```

### Railway Deployment

#### Prerequisites

- Node.js & npm (for Railway CLI)
- Railway account at [railway.app](https://railway.app)

#### Install Railway CLI

```bash
# Using npm (recommended)
npm install -g @railway/cli

# Using yarn
yarn global add @railway/cli

# Using Homebrew (macOS)
brew install railway
```

#### Authenticate with Railway

Railway CLI v4+ uses web-based authentication:

```bash
# Standard login (opens browser)
railway login

# Browserless login (if browser doesn't open)
railway login --browserless

# Verify authentication
railway whoami
```

#### Quick Start with Deployment Script

Use the provided script for guided deployment:

```bash
cd backend
./railway-deploy.sh
```

The script will:
- Check Railway CLI installation
- Verify authentication
- Initialize Railway project
- Configure environment variables
- Deploy your application

#### Manual Deployment

```bash
# 1. Initialize Railway project (first time only)
railway init

# 2. Set environment variables
railway variables set MONGO_URI="mongodb+srv://user:pass@cluster.mongodb.net/saibabasayings"
railway variables set OPENAI_API_KEY="sk-your-openai-api-key"
railway variables set WEAVIATE_URL="https://your-cluster.weaviate.cloud"
railway variables set WEAVIATE_API_KEY="your-weaviate-api-key"
railway variables set FLASK_APP=app.py
railway variables set FLASK_ENV=production
railway variables set FLASK_DEBUG=0
railway variables set FLASK_RUN_PORT=8000
railway variables set FLASK_RUN_HOST=0.0.0.0

# 3. Deploy
railway up

# 4. Monitor deployment
railway logs --follow

# 5. Open deployed app
railway open
```

#### Railway Configuration

The `railway.json` file configures deployment. We recommend NIXPACKS for automatic Python detection:

```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": {
    "builder": "NIXPACKS"
  },
  "deploy": {
    "startCommand": "python app.py",
    "healthcheckPath": "/",
    "healthcheckTimeout": 300,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

**Why NIXPACKS?**
- Automatically detects Python applications
- No Dockerfile required
- Faster build times

#### Useful Railway Commands

```bash
# Deployment
railway up                          # Deploy application
railway status                      # Check deployment status
railway logs                        # View logs
railway logs --follow              # Stream logs
railway open                       # Open deployed app

# Environment variables
railway variables                   # View all variables
railway variables set KEY=value    # Set a variable
railway variables delete KEY       # Delete a variable

# Project management
railway init                       # Initialize project
railway link                       # Link to existing project
railway projects                   # List all projects

# Authentication
railway login                      # Login
railway whoami                     # Check current user
railway logout                     # Logout
```

#### Troubleshooting Railway Deployment

**Authentication Issues:**
```bash
# Re-authenticate
railway logout
railway login

# Use browserless login if browser doesn't open
railway login --browserless
```

**Build Failures:**
```bash
# Check build logs
railway logs --build

# Switch to NIXPACKS if Dockerfile not found
# Edit railway.json: "builder": "NIXPACKS"

# Verify requirements.txt exists
ls requirements.txt
```

**Runtime Errors:**
```bash
# Check application logs
railway logs

# Verify environment variables
railway variables

# Test health endpoint
curl https://your-app.railway.app/
```

**Port Issues:**
- Ensure `FLASK_RUN_PORT=8000` and `FLASK_RUN_HOST=0.0.0.0`
- Railway auto-assigns PORT variable (don't override)

#### Monitoring & Scaling

Railway provides automatic:
- CPU and memory scaling
- Horizontal auto-scaling
- HTTPS encryption
- Health monitoring

Monitor your application in the Railway dashboard for:
- CPU and memory usage
- Request rates
- Deployment history
- Logs and metrics

## Architecture

### Components

```
┌─────────────────┐
│   Frontend      │
│   (React)       │
└────────┬────────┘
         │ HTTP/REST
         ▼
┌─────────────────┐
│  Flask Backend  │◄────── Auth0 (JWT)
│   (app.py)      │
└────────┬────────┘
         │
    ┌────┴────┬──────────���─┬─────────────┐
    ▼         ▼            ▼             ▼
┌────────┐ ┌─────────┐ ┌─────────┐  ┌──────────┐
│MongoDB │ │Weaviate │ │ OpenAI  │  │LangChain │
│(Chats) │ │(Search) │ │ (LLM)   │  │ (Memory) │
└────────┘ └─────────┘ └─────────┘  └──────────┘
```

### Data Flow

1. **Search Request**: User sends query → Backend
2. **Hybrid Search**: Backend queries Weaviate (75% semantic + 25% keyword)
3. **Result Retrieval**: Weaviate returns relevant discourses
4. **Metadata Lookup**: Backend fetches additional metadata from MongoDB (if needed)
5. **Response**: Results returned to frontend

### Key Files

- `app.py` - Main Flask application with all endpoints
- `utils.py` - Helper functions for queries and search
- `weaviate_client.py` - Weaviate integration and hybrid search
- `requirements.txt` - Python dependencies
- `Dockerfile` - Container configuration
- `docker-compose.yml` - Multi-container setup
- `validate-env.py` - Environment validation script
- `railway-deploy.sh` - Railway deployment automation

## Weaviate Hybrid Search

The application uses Weaviate's hybrid search functionality:

### How It Works

- **Vector Search (75%)**: Semantic similarity using OpenAI embeddings
- **Keyword Search (25%)**: BM25 keyword matching
- **Combined Ranking**: Results are ranked by weighted combination

### Configuration

Adjust search parameters in `utils.py`:

```python
results = collection.query.hybrid(
    query=query_text,
    limit=top_k,
    alpha=0.75,  # 0 = keyword only, 1 = vector only
    return_metadata=wvc.query.MetadataQuery(score=True)
)
```

### Schema

Weaviate collection: `SaiBabaDiscourse`

**Properties:**
- `content` (text) - Main discourse content
- `title` (string) - Discourse title
- `mongodb_id` (string) - Original MongoDB ID
- `collection` (string) - Source collection
- `location` (string) - Location of discourse
- `occasion` (string) - Occasion of discourse
- `link` (string) - External link

### Migration

If you need to migrate data from MongoDB to Weaviate:

```bash
python migrate_to_weaviate.py
```

See [WEAVIATE_MIGRATION.md](WEAVIATE_MIGRATION.md) for detailed migration instructions.

## Troubleshooting

### Common Issues

#### 1. Connection Errors

**MongoDB connection refused:**
```bash
# Check MONGO_URI in .env
# Ensure MongoDB is running
# Verify network connectivity
```

**Weaviate connection failed:**
```bash
# Check WEAVIATE_URL and WEAVIATE_API_KEY
# Verify Weaviate instance is running
# Test connection: curl <WEAVIATE_URL>/v1/.well-known/ready
```

**OpenAI API errors:**
```bash
# Verify OPENAI_API_KEY is correct
# Check API key has sufficient credits
# Ensure key has access to required models
```

#### 2. Import Errors

**ModuleNotFoundError:**
```bash
# Activate virtual environment
source venv/bin/activate

# Reinstall dependencies
pip install -r requirements.txt
```

#### 3. Port Already in Use

```bash
# Find process using port 8000
lsof -i :8000

# Kill the process
kill -9 <PID>

# Or change port in .env
FLASK_RUN_PORT=8001
```

#### 4. Docker Issues

**Docker daemon not running:**
```bash
# Start Docker Desktop (macOS/Windows)
# Or start service (Linux): sudo systemctl start docker
```

**Build failures:**
```bash
# Check Dockerfile syntax
# Verify requirements.txt exists
# View build logs for errors
docker-compose logs backend
```

### Debug Commands

```bash
# Check environment
python validate-env.py

# Test health endpoint
curl http://localhost:8000/

# View logs
docker-compose logs -f backend  # Docker
tail -f logs/app.log            # Local

# Check Weaviate health
curl <WEAVIATE_URL>/v1/.well-known/ready

# Test search
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "test"}'
```

### Getting Help

- Check logs for detailed error messages
- Verify all environment variables are set correctly
- Ensure all services (MongoDB, Weaviate) are accessible
- Review deployment guides for platform-specific issues
- Check the issue tracker for known problems

## Project Structure

```
backend/
├── app.py                    # Main Flask application
├── utils.py                  # Query and search utilities
├── weaviate_client.py        # Weaviate integration
├── requirements.txt          # Python dependencies
├── Dockerfile               # Docker container config
├── docker-compose.yml       # Multi-container setup (in root)
├── validate-env.py          # Environment validator
├── railway-deploy.sh        # Railway deployment script
├── railway.json             # Railway configuration
├── railway.env.example      # Environment template
├── .env                     # Environment variables (git-ignored)
├── .dockerignore           # Docker ignore rules
├── .gitignore              # Git ignore rules
├── logs/                   # Application logs
├── venv/                   # Virtual environment
├── README.md               # This file
├── DOCKER_GUIDE.md         # Docker deployment guide
├── RAILWAY_GUIDE.md        # Railway deployment guide
└── WEAVIATE_MIGRATION.md   # Weaviate migration guide
```

## Environment Variables Reference

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `MONGO_URI` | MongoDB connection string | `mongodb+srv://...` |
| `OPENAI_API_KEY` | OpenAI API key | `sk-...` |
| `WEAVIATE_URL` | Weaviate instance URL | `https://cluster.weaviate.cloud` |
| `WEAVIATE_API_KEY` | Weaviate API key | `your_key` |

### Optional Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `FLASK_APP` | Flask app file | `app.py` |
| `FLASK_ENV` | Environment | `production` |
| `FLASK_DEBUG` | Debug mode | `0` |
| `FLASK_RUN_PORT` | Server port | `8000` |
| `FLASK_RUN_HOST` | Server host | `0.0.0.0` |
| `PORT` | Railway/deployment port | Auto-assigned |

## Dependencies

Key Python packages (see `requirements.txt` for full list):

- **flask** - Web framework
- **flask-cors** - CORS support
- **pymongo** - MongoDB driver
- **weaviate-client** - Weaviate integration
- **openai** - OpenAI API
- **langchain** - LLM framework
- **langchain-openai** - OpenAI integration for LangChain
- **langchain-weaviate** - Weaviate integration for LangChain
- **python-dotenv** - Environment variable management
- **pyjwt** - JWT token handling

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature-name`
3. Make your changes
4. Test thoroughly
5. Commit: `git commit -m "Add feature"`
6. Push: `git push origin feature-name`
7. Create a Pull Request

### Development Workflow

```bash
# Setup development environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run tests
python -m pytest

# Run linter
flake8 .

# Run locally
python app.py
```

## License

[Add your license here]

## Support

For issues, questions, or contributions:
- Open an issue in the GitHub repository
- Check existing documentation in `DOCKER_GUIDE.md`, `RAILWAY_GUIDE.md`, and `WEAVIATE_MIGRATION.md`
- Review troubleshooting section above

## Acknowledgments

- Weaviate for hybrid search capabilities
- OpenAI for embeddings and language models
- LangChain for LLM framework
- Sai Baba teachings archive

---

**Built with ❤️ for the Sai Baba community**
