import uuid
from dotenv import load_dotenv
import os
from pymongo import MongoClient
from decouple import config
import configparser
import pymongo
import json
from openai import OpenAI
import logging

# Configure logging
logging.basicConfig(filename='embedding_generation.log', level=logging.ERROR)
load_dotenv()
config = configparser.ConfigParser()
config.read('openai.ini')

# Set up MongoDB connection
client = MongoClient(os.getenv("MONGO_URI"))
db = client.saibabasayings
collection = db.text

# Set up OpenAI client
openai_client = OpenAI(api_key=config['OpenAI']['api_key'])


def get_embedding(text):
    """Generate an embedding for the given text using OpenAI's API."""
    try:
        # Call OpenAI API to get the embedding
        embedding = openai_client.embeddings.create(
            input=text, model="text-embedding-3-large").data[0].embedding
        return embedding
    except Exception as e:
        logging.error(f"Error generating embedding: {e}")
        return None


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
                'index': 'sathyasearch',
                "path": "content_embedding",
                'queryVector': query_embedding,
                'numCandidates': 200,
                'limit': 10
            }
        },
        {
            "$project": {
                "_id": 0,  # Exclude the _id field
                "title": 1,  # Include the plot field
                "content": 1,  # Include the title field
                "score": {
                    "$meta": "vectorSearchScore"  # Include the search score
                }
            }
        }
    ]

    # Execute the search
    results = collection.aggregate(pipeline)
    return list(results)


def handle_user_query(query, collection):

    get_knowledge = search(query, collection)

    search_result = ''
    for result in get_knowledge:
        search_result += f"Title: {result.get('title', 'N/A')}, Content: {result.get('content', 'N/A')}\\n"

    completion = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are an AI assistant designed to help users find spiritual guidance from the teachings of Sathya Sai Baba."},
            {"role": "user", "content": "Answer this user query: " +
             query + " with the following context: " + search_result}
        ]
    )

    return (completion.choices[0].message.content), search_result


# Conduct query with retrieval of sources
query = "When does Sai Baba say is the best time to wake up in the morning?"
response, source_information = handle_user_query(query, collection)

print(f"Response: {response}")
# print(f"Source Information: \\n{source_information}")
