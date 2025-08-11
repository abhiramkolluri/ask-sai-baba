# 🔐 Railway Authentication Guide

## Overview

Railway CLI v4+ uses web-based authentication instead of API key authentication. This guide explains how to authenticate and deploy your backend.

## 🚀 Quick Authentication

### 1. Install Railway CLI
```bash
npm install -g @railway/cli
```

### 2. Login to Railway
```bash
railway login
```

This will:
- Open your browser to Railway's authentication page
- Ask you to authorize the CLI
- Complete the authentication process

### 3. Verify Login
```bash
railway whoami
```

You should see your Railway username if authentication was successful.

## 🔧 Alternative Authentication Methods

### Browserless Login (if browser doesn't open)
```bash
railway login --browserless
```

This will provide a URL and pairing code to complete authentication manually.

### Manual Login Process
1. Run `railway login --browserless`
2. Visit the provided URL in your browser
3. Enter the pairing code
4. Complete the authorization

## 📝 Environment Variables Setup

After authentication, set up your environment variables:

```bash
# Set MongoDB connection
railway variables set MONGO_URI="your_mongodb_connection_string"

# Set OpenAI API key
railway variables set OPENAI_API_KEY="your_openai_api_key"

# Set Flask configuration
railway variables set FLASK_APP=app.py
railway variables set FLASK_ENV=production
railway variables set FLASK_DEBUG=0
railway variables set FLASK_RUN_PORT=8000
railway variables set FLASK_RUN_HOST=0.0.0.0
```

## 🚀 Deployment

Once authenticated and environment variables are set:

```bash
# Deploy your backend
railway up

# Check status
railway status

# View logs
railway logs

# Open deployed app
railway open
```

## 🔍 Troubleshooting

### Authentication Issues
- **"Unauthorized" error**: Run `railway login` again
- **Browser doesn't open**: Use `railway login --browserless`
- **Pairing code expired**: Run `railway login` to get a new code

### Common Commands
```bash
railway logout          # Logout from Railway
railway whoami          # Check current user
railway projects        # List your projects
railway link            # Link to existing project
```

## 📚 Additional Resources

- **Railway Documentation**: [docs.railway.app](https://docs.railway.app)
- **CLI Reference**: [docs.railway.app/reference/cli](https://docs.railway.app/reference/cli)
- **Community**: [discord.gg/railway](https://discord.gg/railway)

---

**Note**: The API key authentication method is no longer supported in Railway CLI v4+. Use the web-based authentication process instead. 