import os
import weaviate
import weaviate.classes as wvc
import logging
from dotenv import load_dotenv
from typing import List, Dict, Any

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class WeaviateManager:
    def __init__(self):
        self.client = None
        self.class_name = "SaiBabaDiscourse"
        self._initialize_client()
        
    def _initialize_client(self):
        """Initialize Weaviate client using v4 API."""
        try:
            # Get configuration from environment variables
            weaviate_url = os.getenv('WEAVIATE_URL', 'http://localhost:8080')
            weaviate_api_key = os.getenv('WEAVIATE_API_KEY')
            openai_api_key = os.getenv('OPENAI_API_KEY')
            
            if not openai_api_key:
                raise ValueError("OPENAI_API_KEY environment variable is required")
            
            # Initialize Weaviate client using v4 API
            if weaviate_api_key:
                # For Weaviate Cloud Services (WCS)
                self.client = weaviate.connect_to_weaviate_cloud(
                    cluster_url=weaviate_url,
                    auth_credentials=wvc.init.Auth.api_key(weaviate_api_key),
                    headers={
                        "X-OpenAI-Api-Key": openai_api_key
                    }
                )
            else:
                # For local Weaviate instances
                self.client = weaviate.connect_to_local(
                    host=weaviate_url.replace('http://', '').replace('https://', ''),
                    headers={
                        "X-OpenAI-Api-Key": openai_api_key
                    }
                )
            
            logger.info("✅ Weaviate client v4 initialized successfully")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize Weaviate client: {e}")
            raise
    
    def create_schema(self):
        """Create the schema for storing Sai Baba discourses with hybrid search support."""
        try:
            # Check if collection already exists
            if self.client.collections.exists(self.class_name):
                logger.info(f"Collection '{self.class_name}' already exists")
                return True
            
            # Create the collection with v4 API - no automatic vectorization
            self.client.collections.create(
                name=self.class_name,
                description="Sai Baba discourses with vector embeddings and hybrid search",
                vectorizer_config=wvc.config.Configure.Vectorizer.none(),  # Disable auto-vectorization
                properties=[
                    wvc.config.Property(
                        name="content",
                        data_type=wvc.config.DataType.TEXT,
                        description="The main content of the discourse"
                    ),
                    wvc.config.Property(
                        name="title", 
                        data_type=wvc.config.DataType.TEXT,
                        description="Title of the discourse"
                    ),
                    wvc.config.Property(
                        name="mongodb_id",
                        data_type=wvc.config.DataType.TEXT,
                        description="Original MongoDB ObjectId",
                        skip_vectorization=True
                    ),
                    wvc.config.Property(
                        name="collection",
                        data_type=wvc.config.DataType.TEXT,
                        description="Source collection",
                        skip_vectorization=True
                    ),
                    wvc.config.Property(
                        name="location",
                        data_type=wvc.config.DataType.TEXT,
                        description="Location where discourse was given",
                        skip_vectorization=True
                    ),
                    wvc.config.Property(
                        name="occasion",
                        data_type=wvc.config.DataType.TEXT,
                        description="Occasion of the discourse",
                        skip_vectorization=True
                    ),
                    wvc.config.Property(
                        name="link",
                        data_type=wvc.config.DataType.TEXT,
                        description="External link to the discourse",
                        skip_vectorization=True
                    )
                ]
            )
            
            logger.info(f"✅ Created collection '{self.class_name}' with hybrid search support")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to create schema: {e}")
            return False
    
    def check_health(self) -> bool:
        """Check if Weaviate is healthy and ready."""
        try:
            health = self.client.is_ready()
            if health:
                logger.info("✅ Weaviate is healthy and ready")
                
                # Check if our collection exists
                if self.client.collections.exists(self.class_name):
                    # Get object count
                    collection = self.client.collections.get(self.class_name)
                    result = collection.aggregate.over_all(total_count=True)
                    count = result.total_count
                    logger.info(f"✅ Found {count} documents in {self.class_name}")
                    return True
                else:
                    logger.warning(f"⚠️  Collection '{self.class_name}' does not exist")
                    return False
            else:
                logger.error("❌ Weaviate is not ready")
                return False
                
        except Exception as e:
            logger.error(f"❌ Weaviate health check failed: {e}")
            return False
    
    def add_documents(self, documents: List[Dict[str, Any]]) -> bool:
        """Add documents to Weaviate using v4 API."""
        try:
            logger.info(f"Adding {len(documents)} documents to Weaviate...")
            
            # Get the collection
            collection = self.client.collections.get(self.class_name)
            
            # Prepare documents for batch insertion
            batch_size = 100
            
            for i in range(0, len(documents), batch_size):
                batch_docs = documents[i:i + batch_size]
                
                # Prepare batch data
                objects = []
                for doc in batch_docs:
                    properties = {
                        "content": str(doc.get('content', '')),
                        "title": str(doc.get('title', 'Untitled')),
                        "mongodb_id": str(doc.get('_id', '')),
                        "collection": str(doc.get('collection', '')),
                        "location": str(doc.get('location', '')),
                        "occasion": str(doc.get('occasion', '')),
                        "link": str(doc.get('link', ''))
                    }
                    
                    # Use pre-computed embedding from MongoDB
                    vector = doc.get('content_embedding')
                    if vector and isinstance(vector, list):
                        objects.append(wvc.data.DataObject(
                            properties=properties,
                            vector=vector
                        ))
                    else:
                        logger.warning(f"No valid embedding found for document {doc.get('_id', 'unknown')}")
                        # Skip documents without embeddings
                        continue
                
                # Insert batch
                response = collection.data.insert_many(objects)
                
                if response.has_errors:
                    for error in response.errors:
                        logger.error(f"Error inserting document: {error}")
                
                logger.info(f"Processed {min(i + batch_size, len(documents))}/{len(documents)} documents")
            
            logger.info(f"✅ Successfully added {len(documents)} documents to Weaviate")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to add documents: {e}")
            return False
    
    def get_document_count(self) -> int:
        """Get the total number of documents."""
        try:
            collection = self.client.collections.get(self.class_name)
            result = collection.aggregate.over_all(total_count=True)
            return result.total_count
        except Exception as e:
            logger.error(f"❌ Failed to get document count: {e}")
            return 0
    
    def close(self):
        """Close the Weaviate connection."""
        if self.client:
            self.client.close()

# Global instance
weaviate_manager = None

def get_weaviate_manager() -> WeaviateManager:
    """Get or create the global Weaviate manager instance."""
    global weaviate_manager
    if weaviate_manager is None:
        weaviate_manager = WeaviateManager()
    return weaviate_manager