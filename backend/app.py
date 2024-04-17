from flask import Flask, url_for, render_template, request, redirect, jsonify
from flask_jwt_extended import JWTManager, jwt_required, create_access_token, get_jwt_identity
from dotenv import load_dotenv
from decouple import config
import configparser
import os
import binascii
from pymongo import MongoClient
import pymongo
from openai import OpenAI
import json
from utils import search

# Initialize Flask app
app = Flask(__name__)


# loading the env variables
load_dotenv()

# Generate a random hex string with 32 bytes
secret_key = binascii.hexlify(os.urandom(32)).decode()

print("Generated JWT Secret Key:", secret_key)


# setting up open ai and mongodb
# client = MongoClient("localhost", 27017)
# client = MongoClient(os.getenv("MONGO_URI"))
client = MongoClient(os.getenv("MONGO_URI"))

db = client.saibabasayings
collection = db.text


# Print out the first few documents in the collection
cursor = collection.find().limit(5)
for doc in cursor:
    print(doc)


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

    return openai_client.embeddings.create(input=[text], model="text-embedding-3-small").data[0].embedding


jwt = JWTManager(app)


@app.route('/', methods=['GET'])
def index():

    return render_template("index.html")


@app.route('/search', methods=['POST'])
def search_endpoint():

    # generate the embedding and return it to the page
    query = request.form['query']
    results = search(model(query), collection)
    return jsonify(results)


# Endpoint for querying Sathya Sai Baba's teachings
@app.route('/api/primarysource/query', methods=['POST'])
@jwt_required()
def query_sai_baba():
    data = request.json
    query = data.get('query')

    # Perform query on MongoDB collection
    result = collection.find_one(
        {'title': {'$regex': query, '$options': 'i'}},
        # {'_id': 0}  # Exclude _id field from the result
    )

    if result:
        # Convert ObjectId to string
        result['_id'] = str(result['_id'])
        return jsonify({'response': result}), 200
    else:
        return jsonify({'message': 'No results found.'}), 404


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
