import os
import json
import datetime
from pymongo import MongoClient
from dotenv import load_dotenv
from weaviate_client import get_client, init_schema

def migrate():
    mongo_uri = os.getenv("MONGO_URI")
    if not mongo_uri:
        print("Error: MONGO_URI not set in environment.")
        return

    try:
        mongo_client = MongoClient(mongo_uri)
        db = mongo_client.saibabasayings
        # Test connection by finding one doc
        db.articles.find_one()
    except Exception as e:
        print(f"Failed to connect to MongoDB or access db.saibabasayings: {e}")
        return

    print("Connected to MongoDB successfully.")
    print("Initializing Weaviate Client and Schema...")
    init_schema()
    
    weaviate_client = get_client()
    if not weaviate_client:
        print("Failed to initialize Weaviate connection. Aborting migration.")
        mongo_client.close()
        return

    def parse_dt(dt_val):
        """Helper to ensure we have a valid python datetime object."""
        if isinstance(dt_val, datetime.datetime):
            # Check for timezone, Weaviate supports UTC offset or naive timezones.
            # Using str replacement to format naive or timezone aware can sometimes be tricky.
            # Convert to UTC or add naive timezone.
            if dt_val.tzinfo is None:
                dt_val = dt_val.replace(tzinfo=datetime.timezone.utc)
            return dt_val
        elif isinstance(dt_val, str):
            try:
                return datetime.datetime.fromisoformat(dt_val.replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.datetime.now(datetime.timezone.utc)

    try:
        # 1. Migrate Articles
        print("Migrating articles...")
        articles_col = weaviate_client.collections.get("Article")
        mongo_articles = list(db.articles.find({}))
        
        with articles_col.batch.dynamic() as batch:
            for article in mongo_articles:
                properties = {
                    "title": article.get("title", ""),
                    "content": article.get("content", ""),
                    "location": article.get("location", ""),
                    "occasion": article.get("occasion", ""),
                    "link": article.get("link", ""),
                    "collection_name": article.get("collection", "")
                }
                batch.add_object(properties=properties)
        print(f"Migrated {len(mongo_articles)} articles.")

        # 2. Migrate Chats -> ChatThread
        print("Migrating chats...")
        chat_thread_col = weaviate_client.collections.get("ChatThread")
        mongo_chats = list(db.chats.find({}))

        with chat_thread_col.batch.dynamic() as batch:
            for chat in mongo_chats:
                created_at = parse_dt(chat.get("timestamp", datetime.datetime.now(datetime.timezone.utc)))
                last_updated = parse_dt(chat.get("last_updated", created_at))
                
                messages = chat.get("messages", [])
                messages_json = json.dumps(messages, default=str)

                properties = {
                    "user_email": chat.get("user_email", ""),
                    "title": chat.get("title", ""),
                    "created_at": created_at,
                    "last_updated": last_updated,
                    "messages_json": messages_json
                }
                batch.add_object(properties=properties)
        print(f"Migrated {len(mongo_chats)} chats.")

        # 3. Migrate Conversations -> Conversation
        print("Migrating conversations...")
        conv_col = weaviate_client.collections.get("Conversation")
        mongo_convs = list(db.conversations.find({}))

        with conv_col.batch.dynamic() as batch:
            for conv in mongo_convs:
                created_at = parse_dt(conv.get("created_at", datetime.datetime.now(datetime.timezone.utc)))
                last_updated = parse_dt(conv.get("last_updated", created_at))
                
                messages = conv.get("messages", [])
                messages_json = json.dumps(messages, default=str)

                properties = {
                    "session_id": conv.get("session_id", ""),
                    "user_id": conv.get("user_id", ""),
                    "messages_json": messages_json,
                    "created_at": created_at,
                    "last_updated": last_updated
                }
                batch.add_object(properties=properties)
        print(f"Migrated {len(mongo_convs)} conversations.")

        # 4. Migrate UserQueries -> UserQuery
        print("Migrating user queries...")
        query_col = weaviate_client.collections.get("UserQuery")
        mongo_queries = list(db.user_queries.find({}))

        with query_col.batch.dynamic() as batch:
            for q in mongo_queries:
                created_at = parse_dt(q.get("timestamp", datetime.datetime.now(datetime.timezone.utc)))
                
                try:
                    score = float(q.get("score", 0.0))
                except (ValueError, TypeError):
                    score = 0.0

                properties = {
                    "query_text": q.get("query_text", ""),
                    "response": q.get("response", ""),
                    "score": score,
                    "citation": q.get("citation", ""),
                    "user_email": q.get("user_email", ""),
                    "created_at": created_at
                }
                batch.add_object(properties=properties)
        print(f"Migrated {len(mongo_queries)} user queries.")

        # 5. Print counts
        print("-" * 50)
        print("Migration Summary")
        print(f"Article count: {articles_col.aggregate.over_all(total_count=True).total_count}")
        print(f"ChatThread count: {chat_thread_col.aggregate.over_all(total_count=True).total_count}")
        print(f"Conversation count: {conv_col.aggregate.over_all(total_count=True).total_count}")
        print(f"UserQuery count: {query_col.aggregate.over_all(total_count=True).total_count}")
        feedback_col = weaviate_client.collections.get("Feedback")
        print(f"Feedback count: {feedback_col.aggregate.over_all(total_count=True).total_count}")
        print("-" * 50)

    except Exception as e:
        print(f"An error occurred during migration: {e}")
        import traceback
        traceback.print_exc()

    finally:
        weaviate_client.close()
        mongo_client.close()
        print("Database connections closed.")

if __name__ == "__main__":
    load_dotenv()
    migrate()