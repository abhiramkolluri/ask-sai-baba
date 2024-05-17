from flask import Flask, url_for, render_template, request, redirect, jsonify
from flask_jwt_extended import JWTManager, jwt_required, create_access_token, get_jwt_identity
from dotenv import load_dotenv
from decouple import config
import configparser
import os
from datetime import datetime  # Import datetime module

import binascii
from pymongo import MongoClient
import pymongo
from openai import OpenAI
import json
# from utils import search
from utils_1 import handle_user_query, search_browse, get_full_article


# Initialize Flask app
app = Flask(__name__)


# loading the env variables
load_dotenv()

# Generate a random hex string with 32 bytes
secret_key = binascii.hexlify(os.urandom(32)).decode()
print("Generated JWT Secret Key:", secret_key)


# setting up open ai and mongodb
client = MongoClient(os.getenv("MONGO_URI"))

db = client.saibabasayings
paragraphs_collection = db.paragraphs
text_collection = db.text


config = configparser.ConfigParser()

# setting up openai
config.read('openai.ini')

app.config['JWT_SECRET_KEY'] = secret_key
# os.getenv(
#     'JWT_SECRET_KEY')  # Use your own secret key
# app.config['JWT_ACCESS_TOKEN_EXPIRES'] = 3600  # 1 hour
openai_client = OpenAI(api_key=config['OpenAI']['api_key'])


# embedding generator
def model(text):

    return openai_client.embeddings.create(input=[text], model="text-embedding-3-large").data[0].embedding


jwt = JWTManager(app)


@app.route('/', methods=['GET'])
def index():

    return render_template("index.html")


def store_user_query(query_text, query_embedding):
    # Get current timestamp
    timestamp = datetime.now()
    # Construct query data
    query_data = {
        'query_text': query_text,
        'query_embedding': query_embedding,
        'timestamp': timestamp
    }
    # Insert query data into MongoDB collection
    db.user_queries.insert_one(query_data)


@app.route('/search', methods=['POST'])
def search_endpoint():
    if request.is_json:
        query = request.json.get('query')
        if query:
            # Generate query embedding
            query_embedding = model(query)
            # Store user query and embedding in the collection
            store_user_query(query, query_embedding)
            # Proceed with search and return results
            results = search_browse(query_embedding, paragraphs_collection)
            return jsonify(results)
        else:
            return jsonify({'error': 'Query parameter is missing'}), 400
    else:
        return jsonify({'error': 'Request must contain JSON data'}), 400


@app.route('/api/primarysource/query', methods=['POST'])
# @jwt_required()
def query_sai_baba():
    try:
        data = request.json
        if not data or 'query' not in data:
            return jsonify({'error': 'Invalid request. Missing or malformed JSON data.'}), 400

        query = data.get('query')
        if not query.strip():
            return jsonify({'error': 'Invalid query. Query cannot be empty.'}), 400

        # store_user_query(query, model(query))

        # Call handle_user_query function to get response and source information
        response, source_information = handle_user_query(
            query, paragraphs_collection)

        return jsonify({'response': response}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/article', methods=['GET'])
def get_article():
    title = request.args.get('title')
    if title:
        article = get_full_article(title, text_collection)
        if article:
            return jsonify(article)
        else:
            return jsonify({'error': 'Article not found'}), 404
    else:
        return jsonify({'error': 'Title parameter is missing'}), 400


@app.route('/register', methods=['POST'])
def register():
    email = request.form.get('email')
    if db.users.find_one({'email': email}):
        return jsonify({'message': 'User already exists'}), 409
    else:
        user_data = {
            'first_name': request.form.get('first_name'),
            'last_name': request.form.get('last_name'),
            'email': email,
            'password': request.form.get('password')
        }
        db.users.insert_one(user_data)
        return jsonify({'message': 'User registered successfully'}), 201


@app.route('/login', methods=['POST'])
def login():
    if request.is_json:
        email = request.json.get('email')
        password = request.json.get('password')

    else:
        email = request.form.get('email')
        password = request.form.get('password')
    user = db.users.find_one({'email': email, 'password': password})
    if user:
        access_token = create_access_token(identity=email)
        return jsonify({'access_token': access_token}), 200
    else:
        return jsonify({'message': 'Invalid credentials'}), 401


if __name__ == "__main__":
    app.run(debug=True)
