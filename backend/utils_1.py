import hashlib
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
collection = db.paragraphs

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
                'index': 'vector_index',
                "path": "paragraph_embedding",
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
    # results = collection.aggregate(pipeline)
    # return list(results)
    results = list(collection.aggregate(pipeline))
    if not results:
        return "No relevant information found in the collection."

    return results


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
    with open('query_finetune.jsonl', 'rb') as f:
        response = openai_client.files.create(file=f, purpose='fine-tune')
        # print(response)
    # return response
        file_id = response.id
        print("*****file_id***** = ", file_id)

        response = openai_client.fine_tuning.jobs.create(
            training_file=file_id,
            model="gpt-3.5-turbo",
        )
        print(response)
        job_id = response.id
        # print("*****job_id***** = ", job_id)

        list_models = openai_client.fine_tuning.jobs.list(limit=1)
        # print("==================================All Models==========================", list_models)
        # openai_client.fine_tuning.jobs.retrieve(job_id)

        finished_at = openai_client.fine_tuning.jobs.retrieve(job_id)
        # print("=================================Finished_At======================= ", finished_at)

        model_id = finished_at
        # print("*****model_id***** = ", model_id)

        # Generate AI response with search context
        completion = openai_client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": "You are an AI assistant designed to help users find spiritual guidance from the teachings of Sathya Sai Baba. I do not have to mention \"According to Sai Baba\" for you to give me an answer. If a question is relevant to the teachings of Sathya Sai Baba, you can answer it."},
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
