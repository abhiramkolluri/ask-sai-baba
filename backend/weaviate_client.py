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
    # Reuse the cached client only if it's still live. is_live() can itself throw
    # when the underlying connection has dropped — treat that as "not live" and
    # fall through to rebuild, rather than letting it propagate as a hard failure.
    if _client is not None:
        try:
            if _client.is_live():
                return _client
        except Exception:
            pass  # stale/broken connection -> reconnect below

    validate_env()
    weaviate_url = os.getenv("WEAVIATE_URL")
    weaviate_api_key = os.getenv("WEAVIATE_API_KEY")
    openai_api_key = os.getenv("OPENAI_API_KEY")

    # OpenAI header powers the existing text2vec-openai collections; the Cohere
    # header (when a key is set) powers the Phase 3 text2vec-cohere Passage_v2
    # collection. Unused vectorizer headers are ignored, so sending both is safe.
    headers = {"X-OpenAI-Api-Key": openai_api_key}
    cohere_key = os.getenv("COHERE_API_KEY")
    if cohere_key:
        headers["X-Cohere-Api-Key"] = cohere_key

    _client = weaviate.connect_to_weaviate_cloud(
        cluster_url=weaviate_url,
        auth_credentials=Auth.api_key(weaviate_api_key),
        headers=headers,
        skip_init_checks=True
    )
    return _client

# Normalized metadata props added to Article and Passage for structured search
# (book/chapter/year filtering). `book` uses FIELD tokenization so equality
# filters match the whole name exactly — with the default word tokenization,
# Equal("Geeta Vahini") would be token-bag matching and cross-match other books.
def _metadata_props():
    return [
        wcd.Property(name="book", data_type=wcd.DataType.TEXT,
                     skip_vectorization=True, tokenization=wcd.Tokenization.FIELD),
        wcd.Property(name="volume", data_type=wcd.DataType.INT),
        wcd.Property(name="chapter_index", data_type=wcd.DataType.INT),
        wcd.Property(name="year", data_type=wcd.DataType.INT),
    ]

def _ensure_props(collection, props):
    # config.get() can fail on client/server version skew (e.g. the client not
    # knowing a newer server's ReplicationDeletionStrategy enum value). Fall
    # back to blindly adding each property and treating "already exists" as
    # success — autoschema must never be what creates these (it would infer
    # the wrong tokenization for FIELD-tokenized props like `book`).
    try:
        existing = {p.name for p in collection.config.get().properties}
    except Exception as e:
        print(f"Could not read {collection.name} config ({e}); adding properties blindly")
        existing = set()
    for prop in props:
        if prop.name in existing:
            continue
        try:
            collection.config.add_property(prop)
            print(f"Added '{prop.name}' property to {collection.name} collection")
        except Exception as e:
            if "already" in str(e).lower() or "exists" in str(e).lower():
                continue
            raise

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
                    wcd.Property(name="collection_name", data_type=wcd.DataType.TEXT, skip_vectorization=True),
                    wcd.Property(name="date", data_type=wcd.DataType.TEXT, skip_vectorization=True),
                    *_metadata_props()
                ]
            )
            print("Created collection 'Article'")
        else:
            article_col = client.collections.get("Article")
            _ensure_props(article_col, [
                wcd.Property(name="date", data_type=wcd.DataType.TEXT, skip_vectorization=True),
                *_metadata_props(),
            ])

        # Create Passage collection (chunked discourse passages for fine-grained search)
        if not client.collections.exists("Passage"):
            client.collections.create(
                name="Passage",
                vectorizer_config=wcd.Configure.Vectorizer.text2vec_openai(
                    model="text-embedding-3-large"
                ),
                properties=[
                    wcd.Property(name="content", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="article_id", data_type=wcd.DataType.TEXT, skip_vectorization=True),
                    wcd.Property(name="chunk_index", data_type=wcd.DataType.INT, skip_vectorization=True),
                    wcd.Property(name="title", data_type=wcd.DataType.TEXT, skip_vectorization=True),
                    wcd.Property(name="link", data_type=wcd.DataType.TEXT, skip_vectorization=True),
                    wcd.Property(name="location", data_type=wcd.DataType.TEXT, skip_vectorization=True),
                    wcd.Property(name="occasion", data_type=wcd.DataType.TEXT, skip_vectorization=True),
                    wcd.Property(name="collection_name", data_type=wcd.DataType.TEXT, skip_vectorization=True),
                    wcd.Property(name="date_authored", data_type=wcd.DataType.TEXT, skip_vectorization=True),
                    *_metadata_props()
                ]
            )
            print("Created collection 'Passage'")
        else:
            _ensure_props(client.collections.get("Passage"), _metadata_props())

        # Entity collection — the structured knowledge layer (Router v2 / Phase 2).
        # Factual/named-text/org-doctrine questions ("Who was Swami's mother?",
        # "Tripura Rahasyam", "Nine Point Code of Conduct") are looked up here
        # instead of being semanticized into spurious thematic matches. Each entry
        # either points at canonical Article(s) or is flagged not-in-corpus so the
        # pipeline can abstain honestly. name/aliases/summary are vectorized for
        # fuzzy match; the rest is metadata.
        if not client.collections.exists("Entity"):
            client.collections.create(
                name="Entity",
                vectorizer_config=wcd.Configure.Vectorizer.text2vec_openai(
                    model="text-embedding-3-large"
                ),
                properties=[
                    wcd.Property(name="name", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="aliases", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="summary", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="entity_type", data_type=wcd.DataType.TEXT, skip_vectorization=True),
                    wcd.Property(name="canonical_article_ids", data_type=wcd.DataType.TEXT, skip_vectorization=True),
                    wcd.Property(name="in_corpus", data_type=wcd.DataType.BOOL, skip_vectorization=True),
                ]
            )
            print("Created collection 'Entity'")

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
                    wcd.Property(name="discourse_title", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="discourse_id", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="discourse_source", data_type=wcd.DataType.TEXT),
                    wcd.Property(name="created_at", data_type=wcd.DataType.DATE)
                ]
            )
            print("Created collection 'Feedback'")
        else:
            # Ensure discourse-context properties exist on the already-created collection.
            feedback_col = client.collections.get("Feedback")
            existing = {p.name for p in feedback_col.config.get().properties}
            for prop in ["discourse_title", "discourse_id", "discourse_source"]:
                if prop not in existing:
                    feedback_col.config.add_property(
                        wcd.Property(name=prop, data_type=wcd.DataType.TEXT)
                    )
                    print(f"Added '{prop}' property to Feedback collection")

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