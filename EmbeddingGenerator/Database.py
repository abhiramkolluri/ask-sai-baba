from dotenv import load_dotenv
import os
import pymongo
import json
import openai
from openai import OpenAI
# from sentence_transformers import SentenceTransformer
from utils import generateEmbeddings, search, insertEmbedding


load_dotenv()

openai.api_key = os.getenv("OPEN_AI")

# client = pymongo.MongoClient(os.getenv("Mongo_uri"))

MONGO_HOST = os.getenv('MONGO_HOST')
MONGO_PORT = int(os.getenv('MONGO_PORT'))  # Convert port to integer
MONGO_AUTH_SOURCE = os.getenv('MONGO_AUTH_SOURCE')
MONGO_USERNAME = os.getenv('MONGO_USERNAME')
MONGO_PASSWORD = os.getenv('MONGO_PASSWORD')
COLLECTION_NAME = 'text'  # Update with your MongoDB collection name


# Use these variables to connect to MongoDB
client = pymongo.MongoClient(host=MONGO_HOST, port=MONGO_PORT, authSource=MONGO_AUTH_SOURCE,
                             username=MONGO_USERNAME, password=MONGO_PASSWORD)
db = client.saibabasayings
collection = db.text


# Load the JSON data
with open('../../ask-sai-baba/Web scraper/data.json', 'r') as file:
    # Load the JSON data
    data = json.load(file)
    for item in data:
        if not collection.find_one(item):
            collection.insert_one(item)


def model(text):
    return openai.embeddings.create(input=[text], model="text-embedding-3-small").data[0].embedding


# create the embedding for the content and insert inside the database
def embeddingGenerator():
    for d in data:
        text = d['Content'].replace("\n", " ")
        # insertEmbedding(collection=collection,model=model,document=d)
        d['content_embedding'] = model(text)
        collection.insert_one(d)

# testing the search functionality
# query = "what is prayer ?"
# search(model(query),collection)
