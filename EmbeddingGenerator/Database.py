from dotenv import load_dotenv
import os
import pymongo
import json
import openai
from openai import OpenAI
# from sentence_transformers import SentenceTransformer
from utils import generateEmbeddings,search,insertEmbedding



load_dotenv()

openai.api_key = os.getenv("OPEN_AI")

client = pymongo.MongoClient(os.getenv("Mongo_uri"))
db = client.saibabasayings
collection = db.text





with open('..\Web scraper\data.json', 'r') as file:
    # Load the JSON data
    data = json.load(file)



def model(text):
    return openai.embeddings.create(input = [text], model="text-embedding-3-small").data[0].embedding

# create the embedding for the content and insert inside the database 
for d in data:
    text = d['Content'].replace("\n", " ")
    # insertEmbedding(collection=collection,model=model,document=d)
    d['content_embedding'] = model(text)
    collection.insert_one(d)

### testing the search functionality
# query = "what is prayer ?"
# search(model(query),collection)