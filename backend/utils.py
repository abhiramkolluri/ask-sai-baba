import numpy as np
import tiktoken
from dotenv import load_dotenv
import os
from pymongo import MongoClient
from bson import ObjectId
import configparser
import pymongo
from openai import OpenAI
import logging
from datetime import datetime
from typing import List, Dict, Any
import json

# Modern LangChain imports
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_mongodb import MongoDBAtlasVectorSearch
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

# Weaviate imports
from weaviate_client import get_weaviate_manager

# Memory imports
from langchain.memory import ConversationBufferWindowMemory, ConversationSummaryBufferMemory
from langchain_core.messages import HumanMessage, AIMessage
from langchain.schema import BaseMessage

from fine_tuning import load_fine_tuned_model_id_from_file

from langchain.chains import SequentialChain
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate

# Configure logging
logging.basicConfig(
    filename='embedding_generation.log', 
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

load_dotenv()
config = configparser.ConfigParser()

# setting up openai - use environment variable first, fallback to config file
openai_api_key = os.getenv('OPENAI_API_KEY')
if not openai_api_key:
    config.read('openai.ini')
    openai_api_key = config['OpenAI']['api_key']

# Set up MongoDB connection
try:
    client = MongoClient(os.getenv("MONGO_URI"))
    db = client.saibabasayings
    article_collection = db.articles
    conversation_collection = db.conversations  # New collection for storing conversations
    mongodb_available = True
    print("✅ MongoDB connection established successfully")
except Exception as e:
    print(f"⚠️  MongoDB connection failed: {e}")
    print("⚠️  Application will run with limited functionality")
    mongodb_available = False
    # Create dummy collections to prevent import errors
    class DummyCollection:
        def count_documents(self, *args, **kwargs):
            return 0
        def find(self, *args, **kwargs):
            return []
        def insert_one(self, *args, **kwargs):
            return None
        def update_one(self, *args, **kwargs):
            return None
        def find_one(self, *args, **kwargs):
            return None
    
    article_collection = DummyCollection()
    conversation_collection = DummyCollection()

# Set up OpenAI client (for backward compatibility)
openai_client = OpenAI(api_key=openai_api_key)

# Initialize LangChain components
embeddings = OpenAIEmbeddings(
    model="text-embedding-3-large",
    api_key=openai_api_key
)

# Initialize ChatOpenAI for memory functionality
chat_model = ChatOpenAI(
    model="gpt-3.5-turbo",
    temperature=0.7,
    api_key=openai_api_key
)

# Initialize Weaviate for vector search operations only
weaviate_manager = None
try:
    weaviate_manager = get_weaviate_manager()
    print("✅ Weaviate manager initialized successfully for vector search")
except Exception as e:
    print(f"⚠️  Weaviate initialization failed: {e}")
    weaviate_manager = None

def check_vector_store_health():
    """Check if Weaviate is properly initialized and has data."""
    if not weaviate_manager:
        print("⚠️  Vector store health check skipped - Weaviate not available")
        return False
        
    try:
        # Use Weaviate's health check
        return weaviate_manager.check_health()
    except Exception as e:
        logging.error(f"Vector store health check failed: {e}")
        return False

# Function to count tokens using the tiktoken library
def count_tokens(text):
    encoding = tiktoken.encoding_for_model("text-embedding-ada-002")
    tokens = encoding.encode(text)
    return len(tokens)

# Function to split text if it exceeds the token limit
def split_text(text, max_tokens=8192):
    if not text:
        return []
    
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=max_tokens,
        chunk_overlap=200,
        length_function=count_tokens,
    )
    return text_splitter.split_text(text)

def model(text):
    """Generate embeddings using LangChain's OpenAI embeddings."""
    try:
        return embeddings.embed_query(text)
    except Exception as e:
        logging.error(f"Error in model function: {e}")
        return None

def get_embedding(text):
    """Generate an embedding for the given text using LangChain."""
    if not text or not isinstance(text, str):
        return None

    try:
        if count_tokens(text) > 8192:
            print("Text too long for document splitting it...")
            text_chunks = split_text(text)
            embeddings_list = [model(chunk) for chunk in text_chunks]
            embedding = np.mean(embeddings_list, axis=0)
        else:
            embedding = model(text)
        
        if isinstance(embedding, np.ndarray):
            embedding = embedding.tolist()
        return embedding
    except Exception as e:
        logging.error(f"Error generating embedding: {e}")
        return None

def weaviate_hybrid_search(query: str, collection, limit: int = 5, alpha: float = 0.5) -> List[Dict[str, Any]]:
    """
    Perform hybrid search using Weaviate's native hybrid search functionality.
    
    Args:
        query: Search query string
        collection: MongoDB collection (kept for fallback compatibility)
        limit: Number of results to return
        alpha: Weighting factor (0 = keyword only, 1 = vector only, 0.5 = balanced 50/50)
    """
    try:
        if not weaviate_manager:
            logging.error("Weaviate manager not available, using MongoDB fallback")
            return mongodb_fallback_search(collection, limit)
        
        logging.info(f"Performing Weaviate hybrid search for: '{query[:50]}...'")
        
        # Get the collection and perform hybrid search using v4 API
        collection = weaviate_manager.client.collections.get(weaviate_manager.class_name)
        
        # Generate embedding for the query since we disabled auto-vectorization
        query_embedding = get_embedding(query)
        if not query_embedding:
            logging.warning("Failed to generate query embedding, using keyword-only search")
            # Fall back to keyword-only search
            response = collection.query.bm25(
                query=query,
                limit=limit,
                return_metadata=["score"]
            )
        else:
            response = collection.query.hybrid(
                query=query,
                vector=query_embedding,
                alpha=alpha,
                limit=limit,
                return_metadata=["score"]
            )
        
        # Process results
        results = []
        for item in response.objects:
            results.append({
                "_id": item.properties.get("mongodb_id"),
                "title": item.properties.get("title", "Untitled"),
                "content": item.properties.get("content", ""),
                "score": item.metadata.score if item.metadata.score else 0.0,
                "location": item.properties.get("location", ""),
                "occasion": item.properties.get("occasion", ""),
                "link": item.properties.get("link", ""),
                "collection": item.properties.get("collection", "")
            })
        
        if results:
            logging.info(f"✅ Weaviate hybrid search returned {len(results)} results")
            return results
        else:
            logging.warning("Weaviate search returned no results, using MongoDB fallback")
            return mongodb_fallback_search(collection, limit)
            
    except Exception as e:
        logging.error(f"❌ Weaviate hybrid search failed: {e}")
        return mongodb_fallback_search(collection, limit)

def mongodb_fallback_search(collection, limit: int = 5) -> List[Dict[str, Any]]:
    """Fallback to MongoDB random search when Weaviate fails."""
    try:
        logging.info("Using MongoDB fallback search")
        random_articles = list(collection.aggregate([
            {"$sample": {"size": limit}},
            {"$project": {
                "_id": 1,
                "title": 1,
                "content": 1,
                "location": 1,
                "occasion": 1,
                "link": 1,
                "collection": 1
            }}
        ]))
        
        results = []
        for article in random_articles:
            results.append({
                "_id": article.get("_id"),
                "collection": article.get("collection", ""),
                "title": article.get("title", "Untitled"),
                "content": article.get("content", "")[:500],
                "score": 0.5,  # Default score for fallback results
                "location": article.get("location", ""),
                "occasion": article.get("occasion", ""),
                "link": article.get("link", "")
            })
        
        return results
    except Exception as e:
        logging.error(f"MongoDB fallback search also failed: {e}")
        return []

def search_browse(embedding, collection):
    """
    Legacy function that now uses Weaviate hybrid search instead of embedding-based search.
    Converts embedding-based search to query-based hybrid search.
    """
    try:
        # For backward compatibility, we'll use a generic query since we can't reverse an embedding to text
        # In practice, this function should be replaced with direct calls to weaviate_hybrid_search
        generic_query = "spiritual guidance teachings wisdom"
        logging.info("Converting embedding search to Weaviate hybrid search with generic query")
        return weaviate_hybrid_search(generic_query, collection)
        
    except Exception as e:
        logging.error(f"Error in search_browse: {e}")
        return mongodb_fallback_search(collection)

def search(user_query: str, collection) -> List[Dict[str, Any]]:
    """Search for documents using Weaviate hybrid search."""
    try:
        # Validate query
        if not user_query or not isinstance(user_query, str):
            logging.error(f"Invalid query: {user_query}")
            raise ValueError(f"Invalid query: {user_query}")
        
        # Use Weaviate hybrid search directly
        return weaviate_hybrid_search(user_query, collection)
        
    except Exception as e:
        logging.error(f"Error in search: {e}")
        return mongodb_fallback_search(collection)

def get_full_article(id, collection):
    article = collection.find_one(
        {"_id": id}, {"_id": 1, "title": 1, "content": 1, "location":1, "occasion": 1, "link": 1, "collection": 1})
    # Convert to markdown format
    markdown_article = f"# {article['title']}\n\n"
    markdown_article += f"**Location:** {article['location']}\n\n"
    markdown_article += f"**Occasion:** {article['occasion']}\n\n"
    markdown_article += f"**Collection:** {article['collection']}\n\n"
    markdown_article += f"**Link:** [{article['link']}]({article['link']})\n\n"
    markdown_article += f"## Content:\n\n{article['content']}\n"
    article['markdown_format'] = markdown_article
    return article

def format_docs(docs):
    """Format documents for context."""
    formatted_docs = []
    
    # Handle empty or None docs
    if not docs:
        logging.warning("No documents provided to format_docs, returning default message")
        return "From discourse 'General Spiritual Guidance': This discourse provides general spiritual guidance and wisdom from Sai Baba's teachings."
    
    for doc in docs:
        if hasattr(doc, 'page_content') and hasattr(doc, 'metadata'):
            # LangChain Document object
            title = doc.metadata.get('title', 'Untitled')
            content = doc.page_content[:500]  # Limit content length
            # Generate a descriptive sentence about how this source answers the query
            description = f"This discourse '{title}' provides insights about the topic by discussing {content[:100]}..."
            formatted_docs.append(f"From discourse '{title}': {description}")
        elif isinstance(doc, dict):
            # Dictionary object
            title = doc.get('title', 'Untitled')
            content = doc.get('content', 'N/A')[:500]  # Limit content length
            # Generate a descriptive sentence about how this source answers the query
            description = f"This discourse '{title}' provides insights about the topic by discussing {content[:100]}..."
            formatted_docs.append(f"From discourse '{title}': {description}")
    
    # Ensure we always return something meaningful
    if not formatted_docs:
        return "From discourse 'General Spiritual Guidance': This discourse provides general spiritual guidance and wisdom from Sai Baba's teachings."
    
    return "\n\n".join(formatted_docs)

def get_or_create_conversation_memory(session_id: str, user_id: str = None) -> ConversationBufferWindowMemory:
    """Get existing conversation memory or create a new one."""
    try:
        # Try to load existing conversation from MongoDB
        conversation_doc = conversation_collection.find_one({
            "session_id": session_id,
            "user_id": user_id
        })
        
        memory = ConversationBufferWindowMemory(
            k=10,  # Remember last 10 exchanges
            return_messages=True,
            memory_key="chat_history"
        )
        
        if conversation_doc and "messages" in conversation_doc:
            # Restore conversation history
            for msg in conversation_doc["messages"]:
                if msg["type"] == "human":
                    memory.chat_memory.add_user_message(msg["content"])
                elif msg["type"] == "ai":
                    memory.chat_memory.add_ai_message(msg["content"])
        
        return memory
    except Exception as e:
        logging.error(f"Error loading conversation memory: {e}")
        # Return empty memory if loading fails
        return ConversationBufferWindowMemory(
            k=10,
            return_messages=True,
            memory_key="chat_history"
        )


def save_conversation_memory(session_id: str, memory: ConversationBufferWindowMemory, user_id: str = None):
    """Save conversation memory to MongoDB."""
    try:
        messages = []
        for message in memory.chat_memory.messages:
            if isinstance(message, HumanMessage):
                messages.append({
                    "type": "human",
                    "content": message.content,
                    "timestamp": datetime.now()
                })
            elif isinstance(message, AIMessage):
                messages.append({
                    "type": "ai", 
                    "content": message.content,
                    "timestamp": datetime.now()
                })
        
        conversation_doc = {
            "session_id": session_id,
            "user_id": user_id,
            "messages": messages,
            "last_updated": datetime.now(),
            "created_at": datetime.now()
        }
        
        # Upsert the conversation
        conversation_collection.update_one(
            {"session_id": session_id, "user_id": user_id},
            {"$set": conversation_doc},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error saving conversation memory: {e}")


def clear_conversation_memory(session_id: str, user_id: str = None):
    """Clear conversation memory for a session."""
    try:
        conversation_collection.delete_one({
            "session_id": session_id,
            "user_id": user_id
        })
        return True
    except Exception as e:
        logging.error(f"Error clearing conversation memory: {e}")
        return False

def handle_user_query(query: str, collection, session_id: str = None, user_id: str = None, user_email: str = None, search_results: List[Dict[str, Any]] = None):
    """Handle user query using LangChain RAG chain with memory support."""
    # Check if the collection is valid
    if not isinstance(collection, pymongo.collection.Collection):
        return "Invalid collection. Please provide a valid MongoDB collection.", ""

    try:
        # Classify the query using the new permissive logic
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

        # Get or create conversation memory
        memory = None
        if session_id:
            memory = get_or_create_conversation_memory(session_id, user_id)

        # Create the system prompt with memory awareness
        system_prompt = """You are an AI assistant that provides spiritual guidance based on Sathya Sai Baba's teachings.

        Your response should be simple and direct:
        "Here are some discourses where you can start learning about the topic:"

        Then list ONLY the actual titles of the discourses provided to you, one per line with a dash, like this:
        - [actual title from the provided discourses]
        - [actual title from the provided discourses]
        - [actual title from the provided discourses]

        Do not provide any descriptions, summaries, or quotes. Do not use placeholder text like "[title of discourse 1]". Use the real titles from the discourses provided.

        Always provide spiritual guidance based on the provided discourses, even if the question seems unrelated. Use the discourses to offer relevant wisdom and insights that can help the user in their spiritual journey.
        """

        # Create the prompt template with memory support
        if memory and memory.chat_memory.messages:
            prompt = ChatPromptTemplate.from_messages([
                ("system", system_prompt),
                ("human", "Previous conversation:\n{chat_history}\n\nNow answer this query: {question}\n\nBased on the following discourses: {context}")
            ])
        else:
            prompt = ChatPromptTemplate.from_messages([
                ("system", system_prompt),
                ("human", "Answer this query: {question}\n\nBased on the following discourses: {context}")
            ])

        # Set up the LLM
        model_id = load_fine_tuned_model_id_from_file()
        llm = ChatOpenAI(
            model=model_id,
            api_key=openai_api_key
        )

        # Use provided search results or generate them if not provided
        if search_results is None:
            # Use Weaviate hybrid search directly with the query
            search_results = weaviate_hybrid_search(query, collection)
        
        # Ensure we have at least 5 results
        search_results = ensure_minimum_results(search_results, collection, min_count=5)
        
        # Format the search results for the prompt
        context = format_docs(search_results)
        
        # Create the prompt template with memory support
        if memory and memory.chat_memory.messages:
            # Format chat history for the prompt
            chat_history = memory.buffer
            prompt = ChatPromptTemplate.from_messages([
                ("system", system_prompt),
                ("human", "Previous conversation:\n{chat_history}\n\nNow answer this query: {question}\n\nBased on the following discourses: {context}")
            ])
            
            # Execute the chain with memory
            response = prompt.invoke({
                "question": query,
                "context": context,
                "chat_history": chat_history
            })
        else:
            prompt = ChatPromptTemplate.from_messages([
                ("system", system_prompt),
                ("human", "Answer this query: {question}\n\nBased on the following discourses: {context}")
            ])
            
            # Execute the chain without memory
            response = prompt.invoke({
                "question": query,
                "context": context
            })
        
        # Get the response from the LLM
        response = llm.invoke(response).content
        
        # Post-process response to remove misunderstanding messages
        if "misunderstanding" in response.lower() or "seems like there might be" in response.lower():
            # Replace with spiritual guidance based on the discourses
            response = "Based on Sai Baba's teachings, here is spiritual guidance that can help you: " + response.split(".")[-1] if "." in response else response
        
        # Save conversation to memory if session_id is provided
        if memory and session_id:
            memory.chat_memory.add_user_message(query)
            memory.chat_memory.add_ai_message(response)
            save_conversation_memory(session_id, memory, user_id)
        
        # Store the query (search_results already obtained above)
        store_new_user_query(query, response, search_results, user_email)
        
        return response
        
    except Exception as e:
        logging.error(f"Error in handle_user_query: {e}")
        # Fallback to direct OpenAI API if LangChain fails
        return handle_user_query_fallback(query, collection)

def handle_user_query_fallback(query: str, collection):
    """Fallback to direct OpenAI API if LangChain fails."""
    try:
        # Get search results using Weaviate hybrid search
        search_results = weaviate_hybrid_search(query, collection)
        
        # Ensure we have at least 5 results
        search_results = ensure_minimum_results(search_results, collection, min_count=5)
        
        # Format context from search results
        context = format_docs(search_results)
        
        # Create the system message
        system_message = """You are an AI assistant that provides spiritual guidance based on Sathya Sai Baba's teachings.

        Your response should be simple and direct:
        "Here are some discourses where you can start learning about the topic:"

        Then list ONLY the actual titles of the discourses provided to you, one per line with a dash, like this:
        - [actual title from the provided discourses]
        - [actual title from the provided discourses]
        - [actual title from the provided discourses]

        Do not provide any descriptions, summaries, or quotes. Do not use placeholder text like "[title of discourse 1]". Use the real titles from the discourses provided.

        Always provide spiritual guidance based on the provided discourses, even if the question seems unrelated. Use the discourses to offer relevant wisdom and insights that can help the user in their spiritual journey.
        """

        # Get the model ID
        model_id = load_fine_tuned_model_id_from_file()
        
        # Make the API call
        response = openai_client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": query},
                {"role": "assistant", "content": "Let me search through Sai Baba's teachings to answer your question."},
                {"role": "user", "content": f"Here are some relevant discourses: {context}"}
            ]
        )
        
        # Extract the response
        answer = response.choices[0].message.content
        
        # Post-process response to remove misunderstanding messages
        if "misunderstanding" in answer.lower() or "seems like there might be" in answer.lower():
            # Replace with spiritual guidance based on the discourses
            answer = "Based on Sai Baba's teachings, here is spiritual guidance that can help you: " + answer.split(".")[-1] if "." in answer else answer
        
        # Store the query (user_email not available in fallback)
        store_new_user_query(query, answer, search_results)
        
        return answer
    except Exception as e:
        logging.error(f"Error in fallback handler: {e}")
        return "An error occurred while processing your query. Please try again.", ""

def ensure_minimum_results(results, collection, min_count=5):
    """Ensure we have at least min_count results, adding random articles if needed."""
    if len(results) >= min_count:
        return results
    
    logging.info(f"Only {len(results)} results found, adding random articles to reach {min_count}")
    
    # Get existing IDs to avoid duplicates
    existing_ids = {str(result.get('_id', '')) for result in results}
    
    # Get additional random articles
    try:
        additional_articles = list(collection.aggregate([
            {"$match": {"_id": {"$nin": [ObjectId(oid) for oid in existing_ids if oid]}}},
            {"$sample": {"size": min_count - len(results)}},
            {"$project": {
                "_id": 1,
                "title": 1,
                "content": 1,
                "location": 1,
                "occasion": 1,
                "link": 1,
                "collection": 1
            }}
        ]))
        
        for article in additional_articles:
            results.append({
                "_id": article.get("_id"),
                "title": article.get("title", "Untitled"),
                "content": article.get("content", "")[:500],
                "score": 0.5,  # Default score for additional results
                "location": article.get("location", ""),
                "occasion": article.get("occasion", ""),
                "link": article.get("link", ""),
                "collection": article.get("collection", "")
            })
    except Exception as e:
        logging.error(f"Error adding additional results: {e}")
    
    return results

def store_new_user_query(query_text, response, get_knowledge, user_email=None):
    print("*** Inside Store new user query method ***")
    print(response)
    timestamp = datetime.now()
    
    citationString = ''
    for knowledge in get_knowledge:
        citationString += f"{knowledge['_id']} -- {knowledge['title']} -- {knowledge['score']}\n"

    try: 
        if get_knowledge:
            score = get_knowledge[0]['score']
            if(score<0.75):
                query_data = {
                    'query_text': query_text,
                    'timestamp': timestamp,
                    'score': score,
                    'citation': citationString,
                    'response': response
                }
                
                # Add user email if provided
                if user_email:
                    query_data['user_email'] = user_email
                
                # Insert query data into MongoDB collection
                db.user_queries.insert_one(query_data)
    except Exception as exp:
        logging.error(f"Error storing user query: {exp}")

def classify_query(query: str) -> tuple[bool, float, str]:
    """
    Classify if a query is inappropriate (vulgar, medical, legal, or financial advice).
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
}

Example medical queries to block:
- "Can you prescribe me medication for anxiety?"
- "What medicine should I take for my headache?"
- "I have a fever, what should I do?"
- "Can you diagnose my symptoms?"

Example allowed queries:
- "What does Sai Baba say about health?"
- "How can I maintain good health?"
- "What is the spiritual significance of illness?"
'''

        # Get the model ID
        model_id = load_fine_tuned_model_id_from_file()
        
        # Make the API call
        response = openai_client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": classification_prompt},
                {"role": "user", "content": query}
            ],
            response_format={"type": "json_object"}
        )
        
        # Parse the response
        result = json.loads(response.choices[0].message.content)
        print(f"Classifier response: {result}")  # Debug print
        
        return result["is_allowed"], result["confidence"], result["reason"]
        
    except Exception as e:
        logging.error(f"Error in classify_query: {e}")
        # If there's an error, allow the query but with low confidence
        return True, 0.5, "Error in classification, defaulting to allow"

# Memory and conversation functionality integrated
# Use handle_user_query with session_id parameter for memory support
