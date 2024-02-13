from dotenv import load_dotenv
import os
import pymongo
from sentence_transformers import SentenceTransformer
from utils import generateEmbeddings,search

model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')


load_dotenv()

client = pymongo.MongoClient(os.getenv("Mongo_uri"))
db = client.sample_mflix
collection = db.movies
testCollection = db.testmovies


query = "can you give me a movie about romance in war"

search(model,query,testCollection)


    