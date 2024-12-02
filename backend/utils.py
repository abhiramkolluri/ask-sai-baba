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

# Set up OpenAI client
openai_client = OpenAI(api_key=config['OpenAI']['api_key'])


# Function to count tokens using the tiktoken library
def count_tokens(text):
    encoding = tiktoken.encoding_for_model("text-embedding-ada-002")
    tokens = encoding.encode(text)
    return len(tokens)


# Function to split text if it exceeds the token limit
def split_text(text, max_tokens=8192):
    encoding = tiktoken.encoding_for_model("text-embedding-ada-002")
    tokens = encoding.encode(text)
    chunks = [tokens[i:i + max_tokens] for i in range(0, len(tokens), max_tokens)]
    return [encoding.decode(chunk) for chunk in chunks]


def model(text):
    return openai_client.embeddings.create(input=[text], model="text-embedding-3-large").data[0].embedding


def get_embedding(text):
    """Generate an embedding for the given text using OpenAI's API."""
    # Check for valid input
    if not text or not isinstance(text, str):
        return None

    try:
        if count_tokens(text) > 8192:
            print("Text too long for document splitting it...")
            # Split the text into chunks if it exceeds the limit
            text_chunks = split_text(text)
            embeddings = [model(chunk) for chunk in text_chunks]
            # You can decide how to handle embeddings: sum, average, or keep them separately
            embedding = np.mean(embeddings, axis=0)  # Averaging embeddings
        else:
            embedding = model(text)
        # Update existing document with new embedding
        if isinstance(embedding, np.ndarray):
            embedding = embedding.tolist()
        return embedding
    except Exception as e:
        logging.error(f"Error generating embedding: {e}")
        return None


def search_browse(embedding, collection):
    # Define the vector search pipeline
    pipeline = [
        {
            "$vectorSearch": {
                'index': 'vector_index',
                "path": "content_embedding",
                'queryVector': embedding,
                'numCandidates': 50,
                'limit': 5
            }
        },
        {
            "$project": {
                "_id": 1,
                "collection": 1,
                "title": 1,  # Include the plot field
                "content": 1,  # Include the title field
                "score": {
                    "$meta": "vectorSearchScore"  # Include the search score
                },
                "location": 1,
                "occasion": 1,
                "link": 1  # Include the article source link
            }
        }
    ]
    results = list(collection.aggregate(pipeline))
    return results


def search(user_query, collection):
    """
    Perform a vector search in the MongoDB collection based on the user query.

    Args:
    user_query (str): The user's query string.
    collection (MongoCollection): The MongoDB collection to search.

    Returns:
    list: A list of matching documents.
    """

    # Generate embedding for the user query
    query_embedding = get_embedding(user_query)
    if query_embedding is None:
        return "Invalid query or embedding generation failed."

    # Define the vector search pipeline
    pipeline = [
        {
            "$vectorSearch": {
                'index': 'vector_index',
                "path": "content_embedding",
                'queryVector': query_embedding,
                'numCandidates': 50,
                'limit': 5
            }
        },
        {
            "$project": {
                "_id": 1,
                "title": 1,
                "content": 1,
                "score": {
                    "$meta": "vectorSearchScore"  # Include the search score
                },
                "location": 1,
                "occasion": 1,
                "link": 1  # Include the article source link
            }
        }
    ]

    # Execute the search
    results = list(collection.aggregate(pipeline))
    return results


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


def handle_user_query(query, collection):
    # Check if the collection is valid
    if not isinstance(collection, pymongo.collection.Collection):
        return "Invalid collection. Please provide a valid MongoDB collection.", ""

    # Retrieve knowledge from the collection
    get_knowledge = search(query, collection)

    # Check if the search process was successful
    if not isinstance(get_knowledge, list):
        return "Search failed. Please try again later.", ""

    search_result = ''
    for result in get_knowledge:
        search_result += f"Content: {result.get('content', 'N/A')}\\n"

    model_id = load_fine_tuned_model_id_from_file()

    # Generate AI response with search context
    completion = openai_client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system",
             "content": "You are an AI assistant designed to help users find spiritual guidance from the teachings of Sathya Sai Baba. I do not have to mention \"According to Sai Baba\" for you to give me an answer. If a question is relevant to the teachings of Sathya Sai Baba, you can answer it. Please avoid using words like \"user\" or \"query\" in your response. If a user asks an irrelevant or out of topic question, then please answer them with, \"It seems like there might be a misunderstanding with the question you provided. I'm here to offer spiritual guidance based on the teachings of Sathya Sai Baba. If you have any questions related to spirituality, personal growth, or Sai Baba's teachings, feel free to ask!\""},
            {"role": "user", "content": "Answer this user query: " + query + " with the following context: " + search_result}
        ]
    )
    response = completion.choices[0].message.content
    store_new_user_query(query,response,get_knowledge)
    return response, search_result

def store_new_user_query(query_text, response, get_knowledge):
    print("*** Inside Store new user query method ***")
    print(response)
    timestamp = datetime.now()
    
    citationString = ''
    for knowledge in get_knowledge:
        citationString += f"{knowledge['_id']} -- {knowledge['title']} -- {knowledge['score']}\n"

    try: 
        score = get_knowledge[0]['score']
        if(score<0.75):
            query_data = {
                'query_text': query_text,
                #'query_embedding': query_embedding,
                'timestamp': timestamp,
                'score': score,
                'citation':citationString,
                'response': response
            }
             #Insert query data into MongoDB collection
            db.user_queries.insert_one(query_data)
    except Exception as exp:
        logging.error(f"Error generating embedding: {exp}")
        

# Conduct query with retrieval of sources
#query = "who is sai baba?"
#response, source_information = handle_user_query(query, article_collection)

#print(f"Response: {response}")
