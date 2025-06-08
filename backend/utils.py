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

# Modern LangChain imports
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import MongoDBAtlasVectorSearch
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from fine_tuning import load_fine_tuned_model_id_from_file

# Configure logging
logging.basicConfig(filename='embedding_generation.log', level=logging.ERROR)

load_dotenv()
config = configparser.ConfigParser()
config.read('../backend/openai.ini')

# Set up MongoDB connection
client = MongoClient(os.getenv("MONGO_URI"))
db = client.saibabasayings
article_collection = db.articles

# Set up OpenAI client (for backward compatibility)
openai_client = OpenAI(api_key=config['OpenAI']['api_key'])

# Initialize LangChain components
embeddings = OpenAIEmbeddings(
    model="text-embedding-3-large",
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
    return "\n\n".join(f"From discourse '{doc.get('title', 'Untitled')}': {doc.get('content', 'N/A')}" for doc in docs)

def handle_user_query(query: str, collection):
    """Handle user query using LangChain RAG chain."""
    if not isinstance(collection, pymongo.collection.Collection):
        return "Invalid collection. Please provide a valid MongoDB collection.", ""

    try:
        # Create the system prompt
        system_prompt = """You are an AI assistant that provides concise spiritual guidance based on Sathya Sai Baba's teachings.

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

        # Create the prompt template
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

        # Create the RAG chain
        rag_chain = (
            {"context": retriever | format_docs, "question": RunnablePassthrough()}
            | prompt
            | llm
            | StrOutputParser()
        )

        # Execute the chain
        response = rag_chain.invoke(query)
        
        # Get search results for storing
        search_results = search(query, collection)
        
        # Store the query
        store_new_user_query(query, response, search_results)
        
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
        store_new_user_query(query, answer, search_results)
        
        return answer, format_docs(search_results)
    except Exception as e:
        logging.error(f"Error in fallback handler: {e}")
        return "An error occurred while processing your query. Please try again.", ""

def store_new_user_query(query_text, response, get_knowledge):
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
                # Insert query data into MongoDB collection
                db.user_queries.insert_one(query_data)
    except Exception as exp:
        logging.error(f"Error storing user query: {exp}")

# Conduct query with retrieval of sources
#query = "who is sai baba?"
#response, source_information = handle_user_query(query, article_collection)

#print(f"Response: {response}")
