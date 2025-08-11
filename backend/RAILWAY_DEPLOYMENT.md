# 🚂 Railway Deployment Guide for Ask Sai Baba Backend

This guide will help you deploy your Ask Sai Baba Backend to Railway, a modern deployment platform that makes it easy to deploy and scale your applications.

## 📋 Prerequisites

1. **Railway Account**: Sign up at [railway.app](https://railway.app)
2. **Railway CLI**: Install the Railway CLI tool
3. **Git Repository**: Your code should be in a Git repository
4. **Environment Variables**: Prepare your configuration values

## 🛠️ Installation

### 1. Install Railway CLI

```bash
# Using npm (recommended)
npm install -g @railway/cli

# Using yarn
yarn global add @railway/cli

# Using Homebrew (macOS)
brew install railway
```

### 2. Login to Railway

```bash
railway login
```

This will open your browser to authenticate with Railway.

## 🚀 Quick Deployment

### Option 1: Using the Deployment Script

```bash
cd backend
./deploy-railway.sh
```

### Option 2: Manual Deployment

```bash
# Navigate to backend directory
cd backend

# Initialize Railway project (if not already done)
railway init

# Set environment variables
railway variables set MONGO_URI="your_mongodb_connection_string"
railway variables set OPENAI_API_KEY="your_openai_api_key"
railway variables set FLASK_APP=app.py
railway variables set FLASK_ENV=production
railway variables set FLASK_DEBUG=0
railway variables set FLASK_RUN_PORT=8000
railway variables set FLASK_RUN_HOST=0.0.0.0

# Deploy
railway up
```

## 🔧 Environment Variables

Set these environment variables in your Railway project:

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `MONGO_URI` | MongoDB connection string | `mongodb+srv://user:pass@cluster.mongodb.net/db` |
| `OPENAI_API_KEY` | Your OpenAI API key | `sk-...` |
| `FLASK_APP` | Flask application file | `app.py` |
| `FLASK_ENV` | Flask environment | `production` |
| `FLASK_DEBUG` | Debug mode | `0` |
| `FLASK_RUN_PORT` | Port to run on | `8000` |
| `FLASK_RUN_HOST` | Host to bind to | `0.0.0.0` |

### Optional Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_LEVEL` | Logging level | `INFO` |
| `LOG_FILE` | Log file name | `embedding_generation.log` |
| `RAILWAY_ENVIRONMENT` | Railway environment | `production` |

## 📁 Project Structure

Your Railway project should have this structure:

```
backend/
├── app.py                 # Main Flask application
├── requirements.txt       # Python dependencies
├── Dockerfile            # Docker configuration
├── railway.json          # Railway configuration
├── .dockerignore         # Docker ignore file
└── ...                   # Other backend files
```

## 🔍 Railway Configuration

The `railway.json` file configures how Railway builds and deploys your app:

```json
{
  "build": {
    "builder": "DOCKERFILE",
    "dockerfilePath": "Dockerfile"
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

## 🚀 Deployment Commands

### Basic Commands

```bash
# Deploy your application
railway up

# Check deployment status
railway status

# View logs
railway logs

# Open deployed app
railway open

# Connect to your project
railway link

# List all projects
railway projects
```

### Environment Management

```bash
# Set a variable
railway variables set KEY=value

# Get all variables
railway variables

# Delete a variable
railway variables delete KEY

# Set variables from file
railway variables set < .env
```

## 📊 Monitoring & Logs

### View Logs

```bash
# View real-time logs
railway logs --follow

# View logs for specific service
railway logs --service backend

# View logs with timestamps
railway logs --timestamps
```

### Health Checks

Railway automatically monitors your application health:
- **Health Check Path**: `/` (root endpoint)
- **Timeout**: 300 seconds
- **Restart Policy**: Restart on failure with max 10 retries

## 🔄 Continuous Deployment

### GitHub Integration

1. Connect your GitHub repository to Railway
2. Railway will automatically deploy on every push to your main branch
3. Configure branch-specific deployments if needed

### Manual Deployment

```bash
# Deploy specific branch
railway up --branch feature-branch

# Deploy with custom message
railway up --message "Deploying new features"
```

## 🚨 Troubleshooting

### Common Issues

1. **Build Failures**
   - Check Dockerfile syntax
   - Verify requirements.txt
   - Check build logs: `railway logs --build`

2. **Runtime Errors**
   - Check application logs: `railway logs`
   - Verify environment variables: `railway variables`
   - Check health check endpoint

3. **Port Issues**
   - Ensure `FLASK_RUN_PORT` is set to `8000`
   - Verify `FLASK_RUN_HOST` is set to `0.0.0.0`

### Debug Commands

```bash
# Check project status
railway status

# View build logs
railway logs --build

# Check environment
railway variables

# Restart service
railway restart
```

## 📈 Scaling

### Auto-scaling

Railway automatically scales your application based on traffic:
- **CPU**: Scales based on usage
- **Memory**: Scales based on requirements
- **Instances**: Auto-scales horizontally

### Manual Scaling

```bash
# Scale to specific number of instances
railway scale 3

# Check current scaling
railway status
```

## 🔐 Security

### Environment Variables

- Never commit sensitive data to Git
- Use Railway's encrypted environment variables
- Rotate API keys regularly

### Network Security

- Railway provides HTTPS by default
- All traffic is encrypted
- No need to configure SSL certificates

## 💰 Cost Optimization

### Resource Management

- Monitor resource usage in Railway dashboard
- Set appropriate resource limits
- Use auto-scaling to optimize costs

### Free Tier

- Railway offers a generous free tier
- Perfect for development and testing
- Upgrade when you need more resources

## 📞 Support

- **Documentation**: [docs.railway.app](https://docs.railway.app)
- **Community**: [discord.gg/railway](https://discord.gg/railway)
- **GitHub**: [github.com/railwayapp](https://github.com/railwayapp)

## 🎯 Next Steps

After successful deployment:

1. **Test your endpoints** using the Railway-provided URL
2. **Set up monitoring** and alerts
3. **Configure custom domain** if needed
4. **Set up CI/CD** for automated deployments
5. **Monitor performance** and optimize as needed

---

**Happy Deploying! 🚂✨** 