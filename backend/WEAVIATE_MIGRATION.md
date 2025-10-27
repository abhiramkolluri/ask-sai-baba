# Weaviate Migration Guide

This document describes how to migrate vector embeddings from MongoDB to Weaviate and configure the application to use Weaviate's hybrid search functionality.

## Prerequisites

1. **Weaviate Instance**: You need a running Weaviate instance. You can use:
   - Local Weaviate (Docker)
   - Weaviate Cloud Services (WCS)
   - Self-hosted Weaviate

2. **Required Dependencies**: Install the additional Python packages:
   ```bash
   pip install weaviate-client==4.9.3 langchain-weaviate==0.0.3
   ```

## Environment Configuration

Add the following environment variables to your `.env` file:

```bash
# Weaviate Configuration
WEAVIATE_URL=http://localhost:8080  # or your Weaviate Cloud URL
WEAVIATE_API_KEY=your_api_key_here  # Optional, only for authenticated instances

# OpenAI API Key (required for embeddings)
OPENAI_API_KEY=your_openai_api_key_here

# MongoDB Configuration (still needed for non-vector operations)
MONGO_URI=your_mongodb_connection_string
```

### Local Weaviate Setup (Docker)

To run Weaviate locally using Docker:

```bash
# Create docker-compose.yml for Weaviate
cat > docker-compose-weaviate.yml << EOF
version: '3.4'
services:
  weaviate:
    command:
    - --host
    - 0.0.0.0
    - --port
    - '8080'
    - --scheme
    - http
    image: semitechnologies/weaviate:1.22.4
    ports:
    - 8080:8080
    restart: on-failure:0
    environment:
      OPENAI_APIKEY: \$OPENAI_APIKEY
      QUERY_DEFAULTS_LIMIT: 25
      AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED: 'true'
      PERSISTENCE_DATA_PATH: '/var/lib/weaviate'
      DEFAULT_VECTORIZER_MODULE: 'text2vec-openai'
      ENABLE_MODULES: 'text2vec-openai'
      CLUSTER_HOSTNAME: 'node1'
    volumes:
    - weaviate_data:/var/lib/weaviate
volumes:
  weaviate_data:
EOF

# Start Weaviate
docker-compose -f docker-compose-weaviate.yml up -d
```

## Migration Process

### Step 1: Run the Migration Script

```bash
cd backend
python migrate_to_weaviate.py
```

The migration script will:
1. Connect to both MongoDB and Weaviate
2. Extract documents with embeddings from MongoDB
3. Create the appropriate schema in Weaviate
4. Transfer all documents to Weaviate
5. Verify the migration was successful

### Step 2: Verify Migration

The script automatically verifies that:
- Document counts match between MongoDB and Weaviate
- Weaviate is healthy and accessible
- Search functionality works correctly

### Step 3: Update Application

The application code has been updated to:
- Use Weaviate for all vector search operations
- Keep MongoDB for user data, conversations, and other non-vector operations
- Automatically fall back to MongoDB random search if Weaviate is unavailable

## Weaviate Schema

The migration creates a schema called `SaiBabaDiscourse` with the following properties:

```json
{
  "class": "SaiBabaDiscourse",
  "vectorizer": "text2vec-openai",
  "properties": [
    {
      "name": "content",
      "dataType": ["text"],
      "description": "The main content of the discourse"
    },
    {
      "name": "title", 
      "dataType": ["string"],
      "description": "Title of the discourse"
    },
    {
      "name": "mongodb_id",
      "dataType": ["string"], 
      "description": "Original MongoDB ObjectId"
    },
    {
      "name": "collection",
      "dataType": ["string"],
      "description": "Source collection"
    },
    {
      "name": "location",
      "dataType": ["string"],
      "description": "Location where discourse was given"
    },
    {
      "name": "occasion",
      "dataType": ["string"],
      "description": "Occasion of the discourse" 
    },
    {
      "name": "link",
      "dataType": ["string"],
      "description": "External link to the discourse"
    }
  ]
}
```

## Hybrid Search Configuration

The application now uses Weaviate's hybrid search functionality, which combines:
- **Vector search**: Semantic similarity using OpenAI embeddings
- **Keyword search**: BM25 keyword matching

The search is configured with `alpha=0.75`, meaning:
- 75% weight on vector/semantic search
- 25% weight on keyword search

You can adjust this balance by modifying the `alpha` parameter in `weaviate_hybrid_search()` function.

## API Changes

### Search Endpoints

The following endpoints now use Weaviate hybrid search:
- `POST /search` - Direct search endpoint
- `POST /query` - Query with response generation

### Backward Compatibility

The migration maintains backward compatibility:
- All existing API endpoints continue to work
- MongoDB is still used for user data, conversations, and metadata
- Fallback mechanisms ensure the app works even if Weaviate is unavailable

## Monitoring and Troubleshooting

### Health Checks

The application includes health checks for Weaviate:
- `check_vector_store_health()` function verifies Weaviate connectivity
- Startup logs show Weaviate initialization status

### Logging

Search operations are logged with details:
- Search queries and results count
- Fallback usage when Weaviate is unavailable
- Error messages for troubleshooting

### Common Issues

1. **Weaviate Connection Failed**
   - Check `WEAVIATE_URL` environment variable
   - Ensure Weaviate is running and accessible
   - Verify network connectivity

2. **OpenAI API Key Issues**
   - Ensure `OPENAI_API_KEY` is set correctly
   - Check API key has sufficient credits
   - Verify key has access to embedding models

3. **Schema Creation Failed**
   - Check Weaviate logs for detailed error messages
   - Ensure proper permissions for schema creation
   - Verify OpenAI integration is configured in Weaviate

4. **Migration Incomplete**
   - Re-run the migration script
   - Check MongoDB connection and document access
   - Verify sufficient disk space in Weaviate

## Performance Benefits

Using Weaviate hybrid search provides:

1. **Better Relevance**: Combines semantic and keyword matching
2. **Faster Search**: Optimized vector operations
3. **Scalability**: Better performance with large datasets  
4. **Flexibility**: Easy to adjust search parameters
5. **Rich Features**: Advanced filtering and search capabilities

## Next Steps

After successful migration:

1. Monitor search performance and relevance
2. Adjust hybrid search parameters if needed
3. Consider implementing additional Weaviate features like:
   - Conditional filtering
   - Multi-vector search
   - Custom distance metrics
4. Optimize vector dimensions for your specific use case