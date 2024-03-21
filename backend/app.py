from flask import Flask, url_for, render_template, request, redirect, jsonify
from dotenv import load_dotenv
import os
from pymongo import MongoClient
import pymongo
import openai
import json
from utils import search

# loading the env variables
load_dotenv()

# setting up open ai and mongodb
openai.api_key = os.getenv("OPEN_AI")
# client = MongoClient("localhost", 27017)
# client = MongoClient(os.getenv("MONGO_URI"))


MONGO_HOST = os.getenv('MONGO_HOST')
MONGO_PORT = int(os.getenv('MONGO_PORT'))  # Convert port to integer
MONGO_AUTH_SOURCE = os.getenv('MONGO_AUTH_SOURCE')
MONGO_USERNAME = os.getenv('MONGO_USERNAME')
MONGO_PASSWORD = os.getenv('MONGO_PASSWORD')


# Use these variables to connect to MongoDB
client = MongoClient(host=MONGO_HOST, port=MONGO_PORT, authSource=MONGO_AUTH_SOURCE,
                     username=MONGO_USERNAME, password=MONGO_PASSWORD)

db = client.saibabasayings
collection = db.text

# embedding generator
def model(text):

    return openai.embeddings.create(input=[text], model="text-embedding-3-small").data[0].embedding


app = Flask(__name__)


@app.route('/', methods=['GET'])
def index():

    return render_template("index.html")


@app.route('/search', methods=['POST'])
def search_endpoint():

    # generate the embedding and return it to the page
    query = request.form['query']
    results = search(model(query), collection)
    return jsonify(results)


if __name__ == "__main__":
    app.run(debug=True)
