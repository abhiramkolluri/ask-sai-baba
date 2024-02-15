from dotenv import load_dotenv
import os
import pymongo
import json
from openai import OpenAI
# from sentence_transformers import SentenceTransformer
from utils import generateEmbeddings,search,insertEmbedding

# model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')


load_dotenv()

# openai.api_key = os.getenv("OPEN_AI")

# client = OpenAI(
#     # This is the default and can be omitted
#     api_key=os.getenv("OPEN_AI"),
# )

client = pymongo.MongoClient(os.getenv("Mongo_uri"))
db = client.saibabasayings
collection = db.text

with open('../data.json') as f:
    data = json.load(f)


collection.insert_many(data)

# query = "can you give me a movie about romance in war"

# search(model,query,testCollection)






# def model(text):
#     return client.embeddings.create(input = [text], model="text-embedding-ada-002").data[0].embedding

    # return client.embeddings.create(input = [text], model="text-embedding-ada-002").data[0].embedding

# for d in data:
#     # print(d['Content'])
#     # text = d['Content'].replace("\n", " ")
#     insertEmbedding(collection=collection,model=model,document=d)
#     # collection.insert_one(d)

# print("finished inserting")
    