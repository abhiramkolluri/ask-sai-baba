from flask_cors import CORS
from flask import Flask, render_template, request, jsonify
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from dotenv import load_dotenv
import configparser
import os
import bcrypt
from datetime import datetime
from bson import ObjectId
from generate_training import summarize_question as generate_summary

import binascii
from pymongo import MongoClient
from openai import OpenAI
from utils import handle_user_query, search_browse, get_full_article, model

# Initialize Flask app
app = Flask(__name__)

# CORS configuration
cors = CORS(app)

# loading the env variables
load_dotenv()

# Generate a random hex string with 32 bytes
secret_key = binascii.hexlify(os.urandom(32)).decode()

# setting up open ai and mongodb
client = MongoClient(os.getenv("MONGO_URI"))

db = client.saibabasayings
article_collection = db.articles
chat_collection = db.chats  # New collection for storing chat threads

config = configparser.ConfigParser()

# setting up openai
config.read('openai.ini')

app.config['JWT_SECRET_KEY'] = secret_key
openai_client = OpenAI(api_key=config['OpenAI']['api_key'])

jwt = JWTManager(app)


@app.route('/', methods=['GET'])
def index():
    return render_template("index.html")


def store_user_query(query_text, query_embedding):
    # Get current timestamap
    timestamp = datetime.now()
    # Construct query data
    query_data = {
        'query_text': query_text,
        'query_embedding': query_embedding,
        'timestamp': timestamp
    }
    # Insert query data into MongoDB collection
    # db.user_queries.insert_one(query_data)


@app.route('/search', methods=['POST'])
def search_endpoint():
    if request.is_json:
        query = request.json.get('query')
        if query:
            # Generate query embedding
            query_embedding = model(query)
            # Store user query and embedding in the collection
            # store_user_query(query, query_embedding)
            # Proceed with search and return results
            results = search_browse(query_embedding, article_collection)
            return jsonify(results)
        else:
            return jsonify({'error': 'Query parameter is missing'}), 400
    else:
        return jsonify({'error': 'Request must contain JSON data'}), 400


@app.route('/query', methods=['POST'])
def query_sai_baba():
    try:
        data = request.json
        if not data or 'query' not in data:
            return jsonify({'error': 'Invalid request. Missing or malformed JSON data.'}), 400

        query = data.get('query')
        if not query.strip():
            return jsonify({'error': 'Invalid query. Query cannot be empty.'}), 400

        # Call handle_user_query function to get response and source information
        response, source_information = handle_user_query(query, article_collection)
        return jsonify({'response': response}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/blog/<id>', methods=['GET'])
def get_article(id):
    if id:
        article = get_full_article(id, article_collection)
        if article:
            return jsonify(article)
        else:
            return jsonify({'error': 'Article not found'}), 404
    else:
        return jsonify({'error': 'ID parameter is missing'}), 400


@app.route('/register', methods=['POST'])
def register():
    email = ""
    password = ""
    first_name = ""
    last_name = ""

    if request.is_json:
        first_name = request.json.get('first_name')
        last_name = request.json.get('last_name')
        email = request.json.get('email')
        password = request.json.get('password')

    else:        
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')
        email = request.form.get('email')
        password = request.form.get('password')

    if first_name is None or first_name == '':
        return jsonify({"error": "First Name is required"}), 400
    if last_name is None or last_name == '':
        return jsonify({"error": "Last Name is required"}), 400
    if email is None or email == '':
        return jsonify({"error": "Email is required"}), 400
    if password is None or password == '':
        return jsonify({"error": "Password is required"}), 400

    if db.users.find_one({'email': email}):
        return jsonify({'message': 'User already exists'}), 409
    else:
        try:
            hashed_password = bcrypt.hashpw(
                password.encode('utf-8'),
                bcrypt.gensalt()
            )
            user_data = {
                'first_name': first_name,
                'last_name': last_name,
                'email': email,
                'password': hashed_password

            }
            db.users.insert_one(user_data)

        except Exception as e:
            print(f"An error occurred: {e}")
            return jsonify({'message': 'Registration unsuccessful'}), 400
        return jsonify({'message': 'User registered successfully'}), 201


@app.route('/login', methods=['POST'])
def login():
    if request.is_json:
        email = request.json.get('email')
        password = request.json.get('password')

    else:
        email = request.form.get('email')
        password = request.form.get('password')
    user = db.users.find_one({'email': email})
    if(user == None):
        return jsonify({'message': 'Incorrect email'}), 401
    
    if user and bcrypt.checkpw(password.encode('utf-8'), user['password']):
        access_token = create_access_token(identity=email)
        return jsonify({'access_token': access_token}), 200
    else:
        return jsonify({'message': 'Invalid credentials'}), 401


# New endpoints for chat management
@app.route('/chats', methods=['GET'])
@jwt_required()
def get_user_chats():
    """Get all chat threads for the logged-in user"""
    try:
        user_email = get_jwt_identity()
        
        # Find all chat threads for this user
        chat_threads = list(chat_collection.find(
            {'user_email': user_email},
            {'_id': 1, 'title': 1, 'timestamp': 1, 'messages': 1}
        ).sort('timestamp', -1))
        
        # Convert ObjectId to string for JSON serialization
        for thread in chat_threads:
            thread['id'] = str(thread['_id'])
            thread['_id'] = str(thread['_id'])
            thread['timestamp'] = thread['timestamp'].isoformat()
        
        return jsonify(chat_threads), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/chats/<thread_id>', methods=['GET'])
@jwt_required()
def get_chat_thread(thread_id):
    """Get a specific chat thread with all its messages"""
    try:
        user_email = get_jwt_identity()
        
        # Find the specific chat thread for this user
        thread = chat_collection.find_one({
            '_id': ObjectId(thread_id),
            'user_email': user_email
        })
        
        if not thread:
            return jsonify({'error': 'Chat thread not found'}), 404
        
        # Convert ObjectId to string for JSON serialization
        thread['id'] = str(thread['_id'])
        thread['_id'] = str(thread['_id'])
        thread['timestamp'] = thread['timestamp'].isoformat()
        
        return jsonify(thread), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/chats', methods=['POST'])
@jwt_required()
def create_chat_thread():
    """Create a new chat thread"""
    try:
        user_email = get_jwt_identity()
        data = request.json
        
        if not data or 'title' not in data:
            return jsonify({'error': 'Title is required'}), 400
        
        thread_data = {
            'user_email': user_email,
            'title': data['title'],
            'timestamp': datetime.now(),
            'messages': []
        }
        
        result = chat_collection.insert_one(thread_data)
        thread_data['id'] = str(result.inserted_id)
        thread_data['_id'] = str(result.inserted_id)
        thread_data['timestamp'] = thread_data['timestamp'].isoformat()
        
        return jsonify(thread_data), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/chats/<thread_id>/messages', methods=['POST'])
@jwt_required()
def add_message_to_thread(thread_id):
    """Add a new message to a chat thread"""
    try:
        user_email = get_jwt_identity()
        data = request.json
        
        if not data or 'question' not in data or 'reply' not in data:
            return jsonify({'error': 'Question and reply are required'}), 400
        
        # Verify the thread belongs to the user
        thread = chat_collection.find_one({
            '_id': ObjectId(thread_id),
            'user_email': user_email
        })
        
        if not thread:
            return jsonify({'error': 'Chat thread not found'}), 404
        
        # Add the new message
        message = {
            'question': data['question'],
            'reply': data['reply'],
            'timestamp': datetime.now()
        }
        
        # Update the thread with the new message
        chat_collection.update_one(
            {'_id': ObjectId(thread_id), 'user_email': user_email},
            {
                '$push': {'messages': message},
                '$set': {'last_updated': datetime.now()}
            }
        )
        
        return jsonify({'message': 'Message added successfully'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/chats/<thread_id>', methods=['PUT'])
@jwt_required()
def update_chat_thread(thread_id):
    """Update a chat thread (e.g., title, messages)"""
    try:
        user_email = get_jwt_identity()
        data = request.json
        
        # Verify the thread belongs to the user
        thread = chat_collection.find_one({
            '_id': ObjectId(thread_id),
            'user_email': user_email
        })
        
        if not thread:
            return jsonify({'error': 'Chat thread not found'}), 404
        
        # Update fields
        update_data = {}
        if 'title' in data:
            update_data['title'] = data['title']
        if 'messages' in data:
            update_data['messages'] = data['messages']
        
        update_data['last_updated'] = datetime.now()
        
        chat_collection.update_one(
            {'_id': ObjectId(thread_id), 'user_email': user_email},
            {'$set': update_data}
        )
        
        return jsonify({'message': 'Chat thread updated successfully'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/chats/<thread_id>', methods=['DELETE'])
@jwt_required()
def delete_chat_thread(thread_id):
    """Delete a chat thread"""
    try:
        user_email = get_jwt_identity()
        
        # Verify the thread belongs to the user and delete it
        result = chat_collection.delete_one({
            '_id': ObjectId(thread_id),
            'user_email': user_email
        })
        
        if result.deleted_count == 0:
            return jsonify({'error': 'Chat thread not found'}), 404
        
        return jsonify({'message': 'Chat thread deleted successfully'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/summarize-question', methods=['POST'])
def summarize_question_endpoint():
    data = request.json
    if not data or 'question' not in data:
        return jsonify({'error': 'Question is required'}), 400
    
    summary = generate_summary(data['question'])
    return jsonify({'summary': summary}), 200


if __name__ == "__main__":
    app.run(debug=True, port=8000)
