#!/usr/bin/env python3
"""
Migration script to transfer vector embeddings from MongoDB to Weaviate.
This script will:
1. Connect to both MongoDB and Weaviate
2. Extract documents with embeddings from MongoDB
3. Transfer them to Weaviate with proper schema
4. Verify the migration was successful
"""

import os
import sys
import logging
from dotenv import load_dotenv
from pymongo import MongoClient
from bson import ObjectId
from tqdm import tqdm
import time

# Add backend to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from weaviate_client import get_weaviate_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('migration.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def connect_to_mongodb():
    """Connect to MongoDB and return the collection."""
    try:
        load_dotenv()
        mongo_uri = os.getenv("MONGO_URI")
        if not mongo_uri:
            raise ValueError("MONGO_URI environment variable is required")
        
        client = MongoClient(mongo_uri)
        db = client.saibabasayings
        collection = db.articles
        
        # Test connection
        count = collection.count_documents({})
        logger.info(f"✅ Connected to MongoDB. Found {count} documents in articles collection")
        
        return collection
    except Exception as e:
        logger.error(f"❌ Failed to connect to MongoDB: {e}")
        raise

def extract_documents_from_mongodb(collection, batch_size=1000):
    """Extract documents from MongoDB in batches."""
    try:
        # Count total documents with embeddings
        total_with_embeddings = collection.count_documents({
            "content_embedding": {"$exists": True, "$ne": None}
        })
        
        total_without_embeddings = collection.count_documents({
            "$or": [
                {"content_embedding": {"$exists": False}},
                {"content_embedding": None}
            ]
        })
        
        logger.info(f"📊 Documents with embeddings: {total_with_embeddings}")
        logger.info(f"📊 Documents without embeddings: {total_without_embeddings}")
        
        if total_with_embeddings == 0:
            logger.warning("⚠️  No documents with embeddings found in MongoDB")
            return []
        
        # Extract documents in batches
        logger.info(f"🔄 Extracting documents from MongoDB in batches of {batch_size}...")
        
        documents = []
        cursor = collection.find({
            "content_embedding": {"$exists": True, "$ne": None}
        }).batch_size(batch_size)
        
        for doc in tqdm(cursor, total=total_with_embeddings, desc="Extracting documents"):
            # Convert ObjectId to string for Weaviate
            doc['_id'] = str(doc['_id'])
            documents.append(doc)
        
        logger.info(f"✅ Extracted {len(documents)} documents from MongoDB")
        return documents
        
    except Exception as e:
        logger.error(f"❌ Failed to extract documents from MongoDB: {e}")
        raise

def migrate_documents_to_weaviate(documents):
    """Migrate documents to Weaviate."""
    try:
        logger.info("🚀 Starting migration to Weaviate...")
        
        # Get Weaviate manager
        weaviate_mgr = get_weaviate_manager()
        
        # Check Weaviate health
        if not weaviate_mgr.check_health():
            logger.info("🔧 Weaviate not ready, creating schema...")
            if not weaviate_mgr.create_schema():
                raise Exception("Failed to create Weaviate schema")
        
        # Check if documents already exist
        existing_count = weaviate_mgr.get_document_count()
        if existing_count > 0:
            logger.warning(f"⚠️  Found {existing_count} existing documents in Weaviate")
            logger.info("🗑️  Automatically deleting existing documents to start fresh...")
            # Delete all existing documents by deleting and recreating the collection
            try:
                weaviate_mgr.client.collections.delete(weaviate_mgr.class_name)
                logger.info("✅ Deleted existing collection")
                # Recreate the schema
                weaviate_mgr.create_schema()
                time.sleep(2)  # Wait for creation to complete
            except Exception as e:
                logger.error(f"❌ Failed to delete existing documents: {e}")
                return False
        
        # Migrate documents
        if documents:
            success = weaviate_mgr.add_documents(documents)
            if not success:
                raise Exception("Failed to add documents to Weaviate")
        
        logger.info("✅ Migration to Weaviate completed successfully!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Migration to Weaviate failed: {e}")
        return False

def verify_migration(original_count):
    """Verify that the migration was successful."""
    try:
        logger.info("🔍 Verifying migration...")
        
        weaviate_mgr = get_weaviate_manager()
        
        # Check document count
        weaviate_count = weaviate_mgr.get_document_count()
        
        logger.info(f"📊 Original MongoDB documents (with embeddings): {original_count}")
        logger.info(f"📊 Weaviate documents: {weaviate_count}")
        
        if weaviate_count == original_count:
            logger.info("✅ Migration verification PASSED: Document counts match!")
            return True
        else:
            logger.error(f"❌ Migration verification FAILED: Count mismatch!")
            return False
            
    except Exception as e:
        logger.error(f"❌ Migration verification failed: {e}")
        return False

def main():
    """Main migration function."""
    weaviate_mgr = None
    try:
        logger.info("🚀 Starting MongoDB to Weaviate migration...")
        
        # Step 1: Connect to MongoDB
        mongo_collection = connect_to_mongodb()
        
        # Step 2: Extract documents from MongoDB
        documents = extract_documents_from_mongodb(mongo_collection)
        
        if not documents:
            logger.warning("⚠️  No documents to migrate. Exiting.")
            return
        
        # Step 3: Migrate to Weaviate
        if migrate_documents_to_weaviate(documents):
            # Step 4: Verify migration
            if verify_migration(len(documents)):
                logger.info("🎉 Migration completed successfully!")
            else:
                logger.error("❌ Migration verification failed!")
                sys.exit(1)
        else:
            logger.error("❌ Migration failed!")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"❌ Migration script failed: {e}")
        sys.exit(1)
    finally:
        # Clean up Weaviate connection
        try:
            weaviate_mgr = get_weaviate_manager()
            if weaviate_mgr:
                weaviate_mgr.close()
        except:
            pass

if __name__ == "__main__":
    main()