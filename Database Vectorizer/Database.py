from dotenv import load_dotenv
import os
import pymongo
import json
from openai import OpenAI
import openai
# from sentence_transformers import SentenceTransformer
from utils import generateEmbeddings,search,insertEmbedding

# model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')


load_dotenv()

openai.api_key = os.getenv("OPEN_AI")

# client = OpenAI(
#     # This is the default and can be omitted
#     api_key=os.getenv("OPEN_AI"),
# )

client = pymongo.MongoClient(os.getenv("Mongo_uri"))
db = client.saibabasayings
collection = db.text









def model(text):
    return openai.embeddings.create(input = [text], model="text-embedding-3-small").data[0].embedding

## create the embedding for the content and insert inside the database 

# for d in data:
#     text = d['Content'].replace("\n", " ")
#     # insertEmbedding(collection=collection,model=model,document=d)
#     d['content_embedding'] = model(text)
#     collection.insert_one(d)

# query = "can you give me a movie about romance in war"

# search(model,query,testCollection)