import os
import sys
from dotenv import load_dotenv
import weaviate.classes.query as wq

# Add current directory to path so we can import weaviate_client
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def main():
    print("Loading environment variables...")
    load_dotenv()
    
    try:
        from weaviate_client import get_client
    except ImportError as e:
        print(f"❌ FAILED to import weaviate_client: {e}")
        print("Ensure you are running this from the backend directory.")
        sys.exit(1)
        
    print("Connecting to Weaviate...")
    try:
        client = get_client()
        if not client:
            print("❌ FAILED: get_client() returned None. Check your env vars.")
            sys.exit(1)
        if not client.is_live():
            print("❌ FAILED: Weaviate client is not live.")
            sys.exit(1)
            
        print("✅ CONNECTED to Weaviate successfully.\n")
    except Exception as e:
        print(f"❌ FAILED to connect: {e}")
        sys.exit(1)

    collections_to_check = ["Article", "ChatThread", "Conversation", "UserQuery", "Feedback", "SavedDiscourse"]
    failures = []
    
    print("--- Collection Status ---")
    
    for coll_name in collections_to_check:
        try:
            if not client.collections.exists(coll_name):
                fails_msg = f"Collection '{coll_name}' DOES NOT EXIST."
                print(f"❌ {fails_msg}")
                failures.append(fails_msg)
                continue
                
            coll = client.collections.get(coll_name)
            # Fetch count
            agg = coll.aggregate.over_all(total_count=True)
            count = agg.total_count
            
            print(f"✅ {coll_name}: {count} documents")
            
            if coll_name == "Article":
                print("\n   --- Article Specific Checks ---")
                try:
                    # Check vectorizer config
                    config = coll.config.get()
                    if config.vectorizer_config is None:
                        fails_msg = "Article collection vectorizer is not configured."
                        print(f"   ❌ {fails_msg}")
                        failures.append(fails_msg)
                    else:
                        vec_type = getattr(config.vectorizer_config, 'vectorizer', getattr(config.vectorizer_config, '__class__', 'Unknown'))
                        print(f"   ✅ Vectorizer Configured: {vec_type}")
                        
                    # Near text search
                    print("   Running sample search for 'love and compassion'...")
                    try:
                        response = coll.query.near_text(
                            query="love and compassion",
                            limit=3,
                            return_metadata=wq.MetadataQuery(distance=True, score=True)
                        )
                        if len(response.objects) > 0:
                            for i, obj in enumerate(response.objects):
                                distance = obj.metadata.distance if obj.metadata else "N/A"
                                title = obj.properties.get('title', 'Unknown Title')
                                print(f"      {i+1}. {title} (Distance/Score: {distance})") # Since score maps onto distance in certain clients
                            print("   ✅ Article search successful.\n")
                        else:
                            print("   ⚠️ Article search returned 0 results (expected if DB is empty).\n")
                    except Exception as e:
                        fails_msg = f"Article near_text search failed: {e}"
                        print(f"   ❌ {fails_msg}")
                        failures.append(fails_msg)
                        
                except Exception as e:
                    fails_msg = f"Failed Article specific checks: {e}"
                    print(f"   ❌ {fails_msg}")
                    failures.append(fails_msg)
                    
        except Exception as e:
            fails_msg = f"Error checking {coll_name}: {e}"
            print(f"❌ {fails_msg}")
            failures.append(fails_msg)
            
    print("\n" + "="*40)
    if not failures:
        print("ALL CHECKS PASSED ✅")
    else:
        print(f"⚠️ COMPLETED WITH {len(failures)} FAILURES:")
        for f in failures:
            print(f" - {f}")

if __name__ == "__main__":
    main()
