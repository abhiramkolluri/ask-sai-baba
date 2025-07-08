import numpy as np
import tiktoken
from dotenv import load_dotenv
import os
from pymongo import MongoClient
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

# Memory imports
from langchain.memory import ConversationBufferWindowMemory, ConversationSummaryBufferMemory
from langchain_core.messages import HumanMessage, AIMessage
from langchain.schema import BaseMessage

from fine_tuning import load_fine_tuned_model_id_from_file

from langchain.chains import SequentialChain
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate

# Configure logging
logging.basicConfig(filename='embedding_generation.log', level=logging.ERROR)

load_dotenv()
config = configparser.ConfigParser()
config.read('openai.ini')

# Set up MongoDB connection
client = MongoClient(os.getenv("MONGO_URI"))
db = client.saibabasayings
article_collection = db.articles
conversation_collection = db.conversations  # New collection for storing conversations

# Set up OpenAI client (for backward compatibility)
openai_client = OpenAI(api_key=config['OpenAI']['api_key'])

# Initialize LangChain components
embeddings = OpenAIEmbeddings(
    model="text-embedding-3-large",
    api_key=config['OpenAI']['api_key']
)

# Initialize ChatOpenAI for memory functionality
chat_model = ChatOpenAI(
    model="gpt-3.5-turbo",
    temperature=0.7,
    api_key=config['OpenAI']['api_key']
)

# Set up vector store
vector_store = MongoDBAtlasVectorSearch(
    collection=article_collection,
    embedding=embeddings,
    index_name="vector_index",
    embedding_key="content_embedding",
    text_key="content"
)

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

def search_browse(embedding, collection):
    """Search for similar documents using LangChain vector store."""
    try:
        # Use LangChain's similarity search
        docs = vector_store.similarity_search_by_vector(
            embedding=embedding,
            k=5
        )
        
        results = []
        for doc in docs:
            # Extract metadata and content
            metadata = doc.metadata
            results.append({
                "_id": metadata.get("_id"),
                "collection": metadata.get("collection"),
                "title": metadata.get("title"),
                "content": doc.page_content,
                "score": metadata.get("score", 0.0),
                "location": metadata.get("location"),
                "occasion": metadata.get("occasion"),
                "link": metadata.get("link")
            })
        
        return results
    except Exception as e:
        logging.error(f"Error in search_browse: {e}")
        return []

def search(user_query: str, collection) -> List[Dict[str, Any]]:
    """Search for documents similar to the query using LangChain."""
    try:
        # Use LangChain's similarity search with score
        docs_with_scores = vector_store.similarity_search_with_score(
            query=user_query,
            k=5
        )
        
        results = []
        for doc, score in docs_with_scores:
            metadata = doc.metadata
            results.append({
                "_id": metadata.get("_id"),
                "title": metadata.get("title"),
                "content": doc.page_content,
                "score": float(score),
                "location": metadata.get("location"),
                "occasion": metadata.get("occasion"),
                "link": metadata.get("link")
            })
        
        return results
    except Exception as e:
        logging.error(f"Error in search: {e}")
        return []

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

def handle_user_query(query: str, collection, session_id: str = None, user_id: str = None, user_email: str = None):
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
        system_prompt = """You are an AI assistant that provides concise spiritual guidance based on Sathya Sai Baba's teachings.

        Provide a clear, summarized response that:
        1. Directly answers the user's question
        2. Includes the most relevant key teachings from Sai Baba
        3. Provides practical guidance when applicable
        4. Keeps the response concise and focused (2-4 sentences maximum)
        5. Only includes the most essential information
        6. If there's conversation history, acknowledge previous discussions when relevant

        For each source provided, include:
        1. A descriptive sentence explaining how that source answers the user's question
        2. The title of the discourse

        Format each source as:
        "From discourse '[title]': [description of how this source answers the question]"

        Base your response on the provided discourses but summarize the key points rather than providing detailed explanations.

        If the question is not related to Sathya Sai Baba's teachings, respond with:
        "I'm here to offer spiritual guidance based on Sai Baba's teachings. Please ask questions related to spirituality, personal growth, or Sai Baba's wisdom. If your question falls into one of the categories above, please ask the question again."
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
            api_key=config['OpenAI']['api_key']
        )

        # Create the retrieval chain
        retriever = vector_store.as_retriever(
            search_type="similarity",
            search_kwargs={"k": 5}
        )

        # Create the RAG chain with memory support
        if memory and memory.chat_memory.messages:
            # Format chat history for the prompt
            chat_history = memory.buffer
            rag_chain = (
                {
                    "context": retriever | format_docs, 
                    "question": RunnablePassthrough(),
                    "chat_history": lambda x: chat_history
                }
                | prompt
                | llm
                | StrOutputParser()
            )
        else:
            rag_chain = (
                {"context": retriever | format_docs, "question": RunnablePassthrough()}
                | prompt
                | llm
                | StrOutputParser()
            )

        # Execute the chain
        response = rag_chain.invoke(query)
        
        # Save conversation to memory if session_id is provided
        if memory and session_id:
            memory.chat_memory.add_user_message(query)
            memory.chat_memory.add_ai_message(response)
            save_conversation_memory(session_id, memory, user_id)
        
        # Get search results for storing
        search_results = search(query, collection)
        
        # Store the query
        store_new_user_query(query, response, search_results, user_email)
        
        return response, format_docs(search_results)
        
    except Exception as e:
        logging.error(f"Error in handle_user_query: {e}")
        # Fallback to direct OpenAI API if LangChain fails
        return handle_user_query_fallback(query, collection)

def handle_user_query_fallback(query: str, collection):
    """Fallback to direct OpenAI API if LangChain fails."""
    try:
        # Get search results
        search_results = search(query, collection)
        
        # Format context from search results
        context = format_docs(search_results)
        
        # Create the system message
        system_message = """You are an AI assistant that provides concise spiritual guidance based on Sathya Sai Baba's teachings.

        Provide a clear, summarized response that:
        1. Directly answers the user's question
        2. Includes the most relevant key teachings from Sai Baba
        3. Provides practical guidance when applicable
        4. Keeps the response concise and focused (2-4 sentences maximum)
        5. Only includes the most essential information

        Base your response on the provided discourses but summarize the key points rather than providing detailed explanations.

        If the question is not related to Sathya Sai Baba's teachings, respond with:
        "I'm here to offer spiritual guidance based on Sai Baba's teachings. Please ask questions related to spirituality, personal growth, or Sai Baba's wisdom. If your question falls into one of the categories above, please ask the question again."
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
        
        # Store the query
        store_new_user_query(query, answer, search_results, user_email)
        
        return answer, format_docs(search_results)
    except Exception as e:
        logging.error(f"Error in fallback handler: {e}")
        return "An error occurred while processing your query. Please try again.", ""

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
