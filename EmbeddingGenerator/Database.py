import uuid
from dotenv import load_dotenv
import os
from pymongo import MongoClient
import configparser
import json
from openai import OpenAI



load_dotenv()
config = configparser.ConfigParser()
config.read('openai.ini')

# openai.api_key = os.getenv("OPEN_AI")
client = MongoClient(os.getenv("MONGO_URI"))
openai_client = OpenAI(api_key=config['OpenAI']['api_key'])


db = client.saibabasayings
collection = db.text


# Load the JSON data
with open('../../ask-sai-baba/Web scraper/data.json', 'r') as file:
    # Load the JSON data
    data = json.load(file)
    for item in data:
        # Ensure each document has an _id field
        if '_id' not in item:
            # Generate a unique identifier if _id is missing
            item['_id'] = str(uuid.uuid4())
        # Check if the document already exists in the collection
        if not collection.find_one({'_id': item['_id']}):
            collection.insert_one(item)


def model(text):
    return openai_client.embeddings.create(input=[text], model="text-embedding-3-large").data[0].embedding


# create the embedding for the content and insert inside the database
def embeddingGenerator(data, model, collection):
    for d in data:
        # Preprocess content (remove newlines)
        # text = d['content'].replace("\n", " ")
        text = d['content'].strip('\n')
        # Optional: Insert original document (if not already stored)
        # insertEmbedding(collection=collection,model=model,document=d)
        # Generate embedding and add it to the document
        embedding = model(text)
        d['content_embedding'] = embedding
        # Update existing document with new embedding
        collection.find_one_and_update({'_id': d['_id']}, {'$set': {'content_embedding': embedding}})
        # collection.insert_one(d)

        # Print generated embedding for reference
        print(f"Generated embedding for document {d['_id']}: {embedding}")

embeddingGenerator(data,model,collection)


# testing the search functionality
# query = "what is prayer ?"
# search(model(query),collection)
