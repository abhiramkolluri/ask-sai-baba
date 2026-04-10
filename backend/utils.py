import os
import json
import logging
from datetime import datetime
from typing import List, Dict, Any
from dotenv import load_dotenv
import configparser

from openai import OpenAI
import weaviate

from fine_tuning import load_fine_tuned_model_id_from_file
from weaviate_client import get_client
from weaviate.classes.query import Filter

# Configure logging
logging.basicConfig(
    filename='embedding_generation.log', 
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

load_dotenv()
config = configparser.ConfigParser()

# Setting up OpenAI
openai_api_key = os.getenv('OPENAI_API_KEY')
if not openai_api_key:
    config.read('openai.ini')
    openai_api_key = config.get('OpenAI', 'api_key', fallback=None)

openai_client = OpenAI(api_key=openai_api_key)

def check_vector_store_health():
    """Check if Weaviate is properly initialized and has data."""
    try:
        client = get_client()
        if client and client.is_live():
            articles = client.collections.get("Article")
            count = articles.aggregate.over_all(total_count=True)
            return count.total_count > 0
        return False
    except Exception as e:
        logging.error(f"Vector store health check failed: {e}")
        return False

def get_embedding(text):
    """Generate an embedding for the given text using OpenAI directly."""
    if not text or not isinstance(text, str):
        return None
    try:
        response = openai_client.embeddings.create(
            model="text-embedding-3-large",
            input=text
        )
        return response.data[0].embedding
    except Exception as e:
        logging.error(f"Error generating embedding: {e}")
        return None

def search_browse(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Search articles using Weaviate's native near_text capabilities."""
    try:
        client = get_client()
        if not client:
            logging.error("Weaviate client not available for search.")
            return []
            
        articles = client.collections.get("Article")
        response = articles.query.near_text(
            query=query,
            limit=limit,
            return_metadata=weaviate.classes.query.MetadataQuery(distance=True)
        )
        
        results = []
        for obj in response.objects:
            results.append({
                "_id": str(obj.uuid),
                "title": obj.properties.get("title", "Untitled"),
                "content": obj.properties.get("content", ""),
                "score": 1 - (obj.metadata.distance or 0),
                "location": obj.properties.get("location", ""),
                "occasion": obj.properties.get("occasion", ""),
                "link": obj.properties.get("link", ""),
                "collection": obj.properties.get("collection_name", ""),
            })
        return results
    except Exception as e:
        logging.error(f"Weaviate search failed: {e}")
        return []

def search_exact(
    query: str,
    limit: int = 5,
    full_match_score: float = 1.0,
    partial_match_score: float = 0.9
) -> List[Dict[str, Any]]:
    """Search articles using exact string matching in title or content."""
    try:
        client = get_client()
        if not client:
            logging.error("Weaviate client not available for exact search.")
            return []
            
        articles = client.collections.get("Article")
        # First try exact match in title
        response = articles.query.fetch_objects(
            filters=Filter.by_property("title").equal(query),
            limit=limit
        )
        
        results = []
        for obj in response.objects:
            results.append({
                "_id": str(obj.uuid),
                "title": obj.properties.get("title", "Untitled"),
                "content": obj.properties.get("content", ""),
                "score": full_match_score,  # Exact match gets perfect score
                "location": obj.properties.get("location", ""),
                "occasion": obj.properties.get("occasion", ""),
                "link": obj.properties.get("link", ""),
                "collection": obj.properties.get("collection_name", ""),
            })
        
        # If no exact title matches, search for substring in content
        if not results:
            response = articles.query.fetch_objects(
                filters=Filter.by_property("content").contains_any([query]),
                limit=limit
            )
            
            for obj in response.objects:
                results.append({
                    "_id": str(obj.uuid),
                    "title": obj.properties.get("title", "Untitled"),
                    "content": obj.properties.get("content", ""),
                    "score": partial_match_score,  # Substring match gets high score
                    "location": obj.properties.get("location", ""),
                    "occasion": obj.properties.get("occasion", ""),
                    "link": obj.properties.get("link", ""),
                    "collection": obj.properties.get("collection_name", ""),
                })
        
        return results[:limit]  # Ensure we don't exceed limit
    except Exception as e:
        logging.error(f"Weaviate exact search failed: {e}")
        return []

def search(user_query: str, collection=None) -> List[Dict[str, Any]]:
    """Search for documents."""
    return search_browse(user_query)

def get_full_article(id, collection=None):
    """Retrieve full article by UUID from Weaviate."""
    try:
        client = get_client()
        articles = client.collections.get("Article")
        
        # UUID parsing
        try:
            import uuid
            uuid_obj = uuid.UUID(id)
        except ValueError:
            logging.error(f"Invalid UUID provided to get_full_article: {id}")
            return None
            
        obj = articles.query.fetch_object_by_id(uuid_obj)
        if not obj:
            return None

        article = {
            "_id": id,
            "title": obj.properties.get("title", ""),
            "content": obj.properties.get("content", ""),
            "location": obj.properties.get("location", ""),
            "occasion": obj.properties.get("occasion", ""),
            "link": obj.properties.get("link", ""),
            "collection": obj.properties.get("collection_name", "")
        }

        # Convert to markdown format
        markdown_article = f"# {article['title']}\n\n"
        markdown_article += f"**Location:** {article['location']}\n\n"
        markdown_article += f"**Occasion:** {article['occasion']}\n\n"
        markdown_article += f"**Collection:** {article['collection']}\n\n"
        markdown_article += f"**Link:** [{article['link']}]({article['link']})\n\n"
        markdown_article += f"## Content:\n\n{article['content']}\n"
        article['markdown_format'] = markdown_article
        
        return article
    except Exception as e:
        logging.error(f"Error fetching article by id: {e}")
        return None

def format_docs(docs):
    """Format documents for context."""
    formatted_docs = []
    if not docs:
        logging.warning("No documents provided to format_docs")
        return "From discourse 'General Spiritual Guidance': This discourse provides general spiritual guidance and wisdom from Sai Baba's teachings."
    
    for doc in docs:
        if isinstance(doc, dict):
            title = doc.get('title', 'Untitled')
            content = str(doc.get('content', 'N/A'))[:500]
            description = f"This discourse '{title}' provides insights about the topic by discussing {content[:100]}..."
            formatted_docs.append(f"From discourse '{title}': {description}")
    
    if not formatted_docs:
        return "From discourse 'General Spiritual Guidance': This discourse provides general spiritual guidance and wisdom from Sai Baba's teachings."
    
    return "\n\n".join(formatted_docs)

def load_conversation_history(session_id: str, user_id: str = None) -> list:
    """Load previous conversation exchanges from Weaviate."""
    try:
        client = get_client()
        conv_col = client.collections.get("Conversation")
        response = conv_col.query.fetch_objects(
            filters=Filter.by_property("session_id").equal(session_id),
            limit=1
        )
        if response.objects:
            obj = response.objects[0]
            messages_json = obj.properties.get("messages_json", "[]")
            messages = json.loads(messages_json)
            formatted = []
            for msg in messages:
                role = "user" if msg.get("type", "").lower() == "human" else "assistant"
                formatted.append({"role": role, "content": msg.get("content", "")})
            return formatted[-20:] # limit to last 10 exchanges
        return []
    except Exception as e:
        logging.error(f"Error loading conversation history: {e}")
        return []

def save_conversation_turn(session_id: str, user_id: str, query: str, answer: str):
    """Save the latest turn to Weaviate."""
    try:
        client = get_client()
        conv_col = client.collections.get("Conversation")
        response = conv_col.query.fetch_objects(
            filters=Filter.by_property("session_id").equal(session_id),
            limit=1
        )
        
        now = datetime.now()
        new_human = {"type": "human", "content": query, "timestamp": now.isoformat()}
        new_ai = {"type": "ai", "content": answer, "timestamp": now.isoformat()}
        
        if response.objects:
            obj = response.objects[0]
            messages_json = obj.properties.get("messages_json", "[]")
            messages = json.loads(messages_json)
            messages.extend([new_human, new_ai])
            
            conv_col.data.update(
                uuid=obj.uuid,
                properties={
                    "messages_json": json.dumps(messages),
                    "last_updated": now
                }
            )
        else:
            messages = [new_human, new_ai]
            conv_col.data.insert(properties={
                "session_id": session_id,
                "user_id": user_id or "",
                "messages_json": json.dumps(messages),
                "created_at": now,
                "last_updated": now
            })
    except Exception as e:
        logging.error(f"save_conversation_turn error: {e}")

def clear_conversation_memory(session_id: str, user_id: str = None):
    """Clear conversation corresponding to a session_id."""
    try:
        client = get_client()
        conv_col = client.collections.get("Conversation")
        conv_col.data.delete_many(where=Filter.by_property("session_id").equal(session_id))
        return True
    except Exception as e:
        logging.error(f"clear_conversation_memory error: {e}")
        return False

def store_new_user_query(query_text, response, get_knowledge, user_email=None):
    """Log the QA search metadata to Weaviate UserQuery."""
    try:
        client = get_client()
        query_col = client.collections.get("UserQuery")
        
        citationString = ''
        score = 0.0
        if get_knowledge:
            score = get_knowledge[0].get('score', 0.0)
            if score < 0.75: # Legacy condition migrated
                for knowledge in get_knowledge:
                    citationString += f"{knowledge.get('_id', '')} -- {knowledge.get('title', '')} -- {knowledge.get('score', 0)}\n"
                    
                query_col.data.insert(properties={
                    "query_text": query_text,
                    "response": response,
                    "score": float(score),
                    "citation": citationString,
                    "user_email": user_email or "",
                    "created_at": datetime.now()
                })
    except Exception as exp:
        logging.error(f"Error storing user query: {exp}")

def classify_query(query: str):
    """
    Classify if a query is inappropriate.
    Returns: (is_allowed, confidence_score, reason)
    """
    try:
        classification_prompt = '''You are a query classifier for an AI assistant. Your task is to block queries that are:
- Vulgar, hateful, or explicit
- Seeking medical advice (e.g., asking for prescriptions, diagnoses, or treatment recommendations)
- Seeking legal advice (e.g., asking for legal interpretations or recommendations)
- Seeking financial advice (e.g., asking for investment, tax, or financial planning advice)
Otherwise, allow the query and provide a broad category (spiritual, personal, general, etc.).

Return your classification as a JSON object with these fields:
{
    "is_allowed": boolean,
    "confidence": float between 0 and 1,
    "category": string (one of: "medical", "legal", "financial", "vulgar", "spiritual", "personal", "general"),
    "reason": string explaining your decision
}'''
        model_id = load_fine_tuned_model_id_from_file()
        response = openai_client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": classification_prompt},
                {"role": "user", "content": query}
            ],
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        return result.get("is_allowed", True), result.get("confidence", 1.0), result.get("reason", "")
    except Exception as e:
        logging.error(f"Error in classify_query: {e}")
        return True, 0.5, "Error in classification"

def handle_user_query(query: str, collection=None, session_id: str = None, user_id: str = None, user_email: str = None, search_results = None):
    """Handle user query completely without LangChain memory structures."""
    try:
        is_allowed, confidence, reason = classify_query(query)
        if not is_allowed:
            suggestion = (
                "Please avoid asking questions that are vulgar, or seek medical, legal, or financial advice. "
                "Try rephrasing your question to focus on spiritual, personal, or general topics. "
                "If you believe your question is valid and this was a mistake, please try asking again in a different way, as the website can sometimes make errors."
            )
            return (
                f"I'm sorry, but I cannot answer this question. Reason: {reason}\n"
                f"{suggestion}",
                ""
            )

        # Check if query is in quotes for exact search
        is_exact_search = query.startswith('"') and query.endswith('"')
        if is_exact_search:
            query_text = query[1:-1]  # Remove quotes
            if search_results is None:
                search_results = search_exact(query_text, limit=5)
        else:
            if search_results is None:
                search_results = search_browse(query, limit=5)
            
        # Ensure we have at least 5 results (by grabbing random docs if search fails)
        # Assuming we aren't performing random augmentation here anymore due to semantic search efficiency,
        # but the fallback might just use what we have. 
            
        context = format_docs(search_results)
        
        chat_history = []
        if session_id:
            chat_history = load_conversation_history(session_id, user_id)

        system_prompt = """You are an AI assistant that provides spiritual guidance based on Sathya Sai Baba's teachings.

        Your response should be simple and direct:
        "Here are some discourses where you can start learning about the topic:"

        Then list ONLY the actual titles of the discourses provided to you, one per line with a dash, like this:
        - [actual title from the provided discourses]
        - [actual title from the provided discourses]
        - [actual title from the provided discourses]

        Do not provide any descriptions, summaries, or quotes. Do not use placeholder text like "[title of discourse 1]". Use the real titles from the discourses provided.

        Always provide spiritual guidance based on the provided discourses, even if the question seems unrelated. Use the discourses to offer relevant wisdom and insights that can help the user in their spiritual journey."""

        messages = [{"role": "system", "content": system_prompt}]
        for msg in chat_history:
            messages.append(msg)
            
        messages.append({
            "role": "user",
            "content": f"Answer this query: {query}\n\nBased on the following discourses: {context}"
        })

        model_id = load_fine_tuned_model_id_from_file()
        response = openai_client.chat.completions.create(
            model=model_id,
            messages=messages
        )
        answer = response.choices[0].message.content

        # Post-process message
        if "misunderstanding" in answer.lower() or "seems like there might be" in answer.lower():
            answer = "Based on Sai Baba's teachings, here is spiritual guidance that can help you: " + (answer.split(".")[-1] if "." in answer else answer)

        if session_id:
            save_conversation_turn(session_id, user_id, query, answer)

        store_new_user_query(query, answer, search_results, user_email)

        return answer
    except Exception as e:
        logging.error(f"Error in handle_user_query: {e}")
        return "An error occurred while processing your query.", ""
