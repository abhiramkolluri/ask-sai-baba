import numpy as np
import tiktoken
from dotenv import load_dotenv
import os
from pymongo import MongoClient
import configparser
import pymongo
from openai import OpenAI
import logging

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
    return article


def load_fine_tuned_model_id_from_file():
    model_file_path = 'fine_tuned_model.txt'
    if os.path.exists(model_file_path):
        with open(model_file_path, 'r') as f:
            return f.read().strip()
    else:
        return None


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

    jsonl_file = 'query_finetune.jsonl'
    jsonl_last_modified = os.path.getmtime(jsonl_file)

    # Check if fine-tuning is required
    fine_tuning_required = False
    list_models = openai_client.fine_tuning.jobs.list(limit=1)

    if not list_models or not list_models.data:
        fine_tuning_required = True
        print("No previous models found. Creating new model.")
    else:
        last_model = list_models.data[0]
        last_model_finished_at = last_model.finished_at

        # If the last model finished time is None or the JSONL file has been modified since the last model finished
        if last_model_finished_at is None or last_model_finished_at < jsonl_last_modified:
            fine_tuning_required = True
            print("JSONL file updated. Creating new model.")
        else:
            print("JSONL file hasn't been updated in a while. Using old model.")

    if fine_tuning_required:
        with open(jsonl_file, 'rb') as f:
            response = openai_client.files.create(file=f, purpose='fine-tune')
            file_id = response.id

        response = openai_client.fine_tuning.jobs.create(training_file=file_id,model="gpt-3.5-turbo-0125")
        job_id = response.id

        # Wait until the job finishes
        while True:
            job_status = openai_client.fine_tuning.jobs.retrieve(job_id)
            if job_status.status == 'succeeded':
                fine_tuned_model_id = job_status.fine_tuned_model
                print("Fine-tuned model created successfully.",
                      fine_tuned_model_id)
                with open('fine_tuned_model.txt', 'w') as model_file:
                    model_file.write(fine_tuned_model_id)
                break
            elif job_status.status in ['failed', 'cancelled']:
                print(f"Fine-tuning job {job_status.status}. Exiting loop.")
                return "Fine-tuning job did not succeed.", ""

        model_id = fine_tuned_model_id
    else:
        # Use the last fine-tuned model
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
    return completion.choices[0].message.content, search_result


# Conduct query with retrieval of sources
#query = "who is sai baba?"
#response, source_information = handle_user_query(query, article_collection)

#print(f"Response: {response}")
