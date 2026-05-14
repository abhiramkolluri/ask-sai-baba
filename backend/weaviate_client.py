import os
import weaviate
from weaviate.classes.init import Auth
import weaviate.classes.config as wcd

_client = None

def validate_env():
    weaviate_url = os.getenv("WEAVIATE_URL")
    weaviate_api_key = os.getenv("WEAVIATE_API_KEY")
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not weaviate_url:
        raise ValueError("Missing required environment variable: WEAVIATE_URL")
    if not weaviate_api_key:
        raise ValueError("Missing required environment variable: WEAVIATE_API_KEY")
    if not openai_api_key:
        raise ValueError("Missing required environment variable: OPENAI_API_KEY")

def get_client():
    global _client
    if _client is not None and _client.is_live():
        return _client
        
    validate_env()
    weaviate_url = os.getenv("WEAVIATE_URL")
    weaviate_api_key = os.getenv("WEAVIATE_API_KEY")
    openai_api_key = os.getenv("OPENAI_API_KEY")

    _client = weaviate.connect_to_weaviate_cloud(
        cluster_url=weaviate_url,
        auth_credentials=Auth.api_key(weaviate_api_key),
        headers={"X-OpenAI-Api-Key": openai_api_key}
    )
    return _client

def init_schema():
    client = get_client()
    if not client:
        print("Could not connect to Weaviate to initialize schema.")
        return

    try:
        # Create Article collection
        if not client.collections.exists("Article"):
            client.collections.create(
                name="Article",
                vectorizer_config=wcd.Configure.Vectorizer.text2vec_openai(
                    model="text-embedding-3-large"
                ),
                properties=[
                    wcd.Property(name="title", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="content", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="location", data_type=wcd.DataType.TEXT, skip_vectorization=True),
                    wcd.Property(name="occasion", data_type=wcd.DataType.TEXT, skip_vectorization=True),
                    wcd.Property(name="link", data_type=wcd.DataType.TEXT, skip_vectorization=True),
                    wcd.Property(name="collection_name", data_type=wcd.DataType.TEXT, skip_vectorization=True)
                ]
            )
            print("Created collection 'Article'")

        # Create ChatThread collection
        if not client.collections.exists("ChatThread"):
            client.collections.create(
                name="ChatThread",
                properties=[
                    wcd.Property(name="user_email", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="title", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="created_at", data_type=wcd.DataType.DATE),
                    wcd.Property(name="last_updated", data_type=wcd.DataType.DATE),
                    wcd.Property(name="messages_json", data_type=wcd.DataType.TEXT)
                ]
            )
            print("Created collection 'ChatThread'")

        # Create Conversation collection
        if not client.collections.exists("Conversation"):
            client.collections.create(
                name="Conversation",
                properties=[
                    wcd.Property(name="session_id", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="user_id", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="messages_json", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="last_updated", data_type=wcd.DataType.DATE),
                    wcd.Property(name="created_at", data_type=wcd.DataType.DATE)
                ]
            )
            print("Created collection 'Conversation'")

        # Create UserQuery collection
        if not client.collections.exists("UserQuery"):
            client.collections.create(
                name="UserQuery",
                properties=[
                    wcd.Property(name="query_text", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="response", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="score", data_type=wcd.DataType.NUMBER),
                    wcd.Property(name="citation", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="user_email", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="created_at", data_type=wcd.DataType.DATE)
                ]
            )
            print("Created collection 'UserQuery'")

        # Create Feedback collection
        if not client.collections.exists("Feedback"):
            client.collections.create(
                name="Feedback",
                properties=[
                    wcd.Property(name="question", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="answer", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="feedback_type", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="reason", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="additional_comments", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="created_at", data_type=wcd.DataType.DATE)
                ]
            )
            print("Created collection 'Feedback'")

        # Create SavedDiscourse collection
        if not client.collections.exists("SavedDiscourse"):
            client.collections.create(
                name="SavedDiscourse",
                properties=[
                    wcd.Property(name="user_email", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="article_uuid", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="title", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="content_preview", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="link", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="collection_name", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="saved_at", data_type=wcd.DataType.DATE),
                    wcd.Property(name="highlights_json", data_type=wcd.DataType.TEXT)
                ]
            )
            print("Created collection 'SavedDiscourse'")

        # Create UserAccount collection for manual auth
        if not client.collections.exists("UserAccount"):
            client.collections.create(
                name="UserAccount",
                properties=[
                    wcd.Property(name="first_name", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="last_name", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="email", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="password_hash", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="auth_provider", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="created_at", data_type=wcd.DataType.DATE)
                ]
            )
            print("Created collection 'UserAccount'")

        # Create PasswordResetToken collection for manual password reset
        if not client.collections.exists("PasswordResetToken"):
            client.collections.create(
                name="PasswordResetToken",
                properties=[
                    wcd.Property(name="user_email", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="token_hash", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="created_at", data_type=wcd.DataType.DATE),
                    wcd.Property(name="expires_at", data_type=wcd.DataType.DATE),
                    wcd.Property(name="used", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="used_at", data_type=wcd.DataType.DATE)
                ]
            )
            print("Created collection 'PasswordResetToken'")

    except Exception as e:
        print(f"Error initializing schema: {e}")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    print("Initializing Weaviate schema...")
    init_schema()
    if _client is not None:
        _client.close()