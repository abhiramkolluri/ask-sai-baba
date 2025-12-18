from flask_cors import CORS
from flask import Flask, request, jsonify, redirect
from flask_jwt_extended import JWTManager, create_access_token
from flask_mail import Mail, Message
from dotenv import load_dotenv
import configparser
import os
from datetime import datetime, timedelta
from bson import ObjectId
import jwt
import bcrypt
import secrets
import hashlib

from pymongo import MongoClient
from openai import OpenAI
from utils import handle_user_query, get_full_article, clear_conversation_memory, check_vector_store_health

# Google OAuth imports
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from google_auth_oauthlib.flow import Flow
import json

# Initialize Flask app
app = Flask(__name__)

# JWT configuration
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'your-secret-key-change-this')  # Change this in production
jwt_manager = JWTManager(app)

# CORS configuration
cors = CORS(app, resources={
    r"/*": {
        "origins": [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "https://develop.d1zpscp56yzv1s.amplifyapp.com"
        ],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

# loading the env variables
load_dotenv()

# setting up open ai and mongodb
try:
    client = MongoClient(os.getenv("MONGO_URI"))
    db = client.saibabasayings
    article_collection = db.articles
    chat_collection = db.chats  # New collection for storing chat threads
    users_collection = db.users  # Collection for storing user data
    saved_discourses_collection = db.saved_discourses  # Collection for saved discourses
    mongodb_available = True
    print("✅ MongoDB connection established successfully in app.py")
except Exception as e:
    print(f"⚠️  MongoDB connection failed in app.py: {e}")
    print("⚠️  Application will run with limited functionality")
    mongodb_available = False
    # Create dummy collections to prevent import errors
    class DummyCollection:
        def count_documents(self, *args, **kwargs):
            return 0
        def find(self, *args, **kwargs):
            return []
        def insert_one(self, *args, **kwargs):
            return None
        def update_one(self, *args, **kwargs):
            return None
        def find_one(self, *args, **kwargs):
            return None
    
    article_collection = DummyCollection()
    chat_collection = DummyCollection()
    users_collection = DummyCollection()

config = configparser.ConfigParser()

# setting up openai - use environment variable first, fallback to config file
openai_api_key = os.getenv('OPENAI_API_KEY')
if not openai_api_key:
    config.read('openai.ini')
    openai_api_key = config['OpenAI']['api_key']

openai_client = OpenAI(api_key=openai_api_key)

# Email configuration for password reset
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True') == 'True'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER', os.getenv('MAIL_USERNAME'))

mail = Mail(app)

# Check vector store health on startup
print("Checking vector store health...")
vector_store_healthy = check_vector_store_health()
if vector_store_healthy:
    print("✅ Vector store is healthy and ready for searches")
else:
    print("⚠️  Vector store health check failed - searches may fall back to random results")

def get_user_email_from_auth0_token(token):
    """Extract user email from Auth0 token"""
    try:
        # First, let's see what the token looks like
        print(f"Full token: {token}")
        print(f"Token length: {len(token)}")
        
        # Split the token and decode the payload part
        parts = token.split('.')
        print(f"Token has {len(parts)} parts")
        
        # First, let's decode the header to understand the token structure
        import base64
        import json
        
        try:
            # Decode the header (first part)
            header = parts[0]
            missing_padding = len(header) % 4
            if missing_padding:
                header += '=' * (4 - missing_padding)
            
            header_bytes = base64.urlsafe_b64decode(header)
            header_json = json.loads(header_bytes.decode('utf-8'))
            print(f"JWT Header: {header_json}")
            
            # Check if this is an encrypted JWT (JWE)
            if header_json.get('enc'):
                print(f"This is an encrypted JWT with encryption: {header_json.get('enc')}")
                # For encrypted JWTs, we need to use a different approach
                # Let's try to get the user info from the Auth0 user object instead
                return None
                
        except Exception as header_error:
            print(f"Failed to decode header: {header_error}")
        
        # This looks like an encrypted JWT (JWE) - try to decode it properly
        try:
            # Try to decode as JWT without verification
            decoded_auth0 = jwt.decode(token, options={
                "verify_signature": False, 
                "verify_aud": False, 
                "verify_iss": False,
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iat": False,
                "verify_jti": False
            })
            email = decoded_auth0.get('email') or decoded_auth0.get('sub')
            print(f"Successfully decoded JWT token for user: {email}")
            return email
        except Exception as jwt_error:
            print(f"JWT decoding failed: {jwt_error}")
        
        # If JWT decoding fails, try base64 approach as fallback
        # For JWT tokens, we expect 3 parts (header.payload.signature)
        # But Auth0 might send different formats
        if len(parts) >= 2:  # At least header and payload
            # Try the second part as payload (index 1)
            payload = parts[1]
            # Fix padding properly
            missing_padding = len(payload) % 4
            if missing_padding:
                payload += '=' * (4 - missing_padding)
            
            try:
                decoded_bytes = base64.urlsafe_b64decode(payload)
                decoded = json.loads(decoded_bytes.decode('utf-8'))
                email = decoded.get('email') or decoded.get('sub')
                print(f"Successfully decoded token using base64 for user: {email}")
                return email
            except Exception as base64_error:
                print(f"Base64 decoding failed for part 1: {base64_error}")
                
                # Try other parts if the second part fails
                for i, part in enumerate(parts):
                    if i == 1:  # Skip the first part we already tried
                        continue
                    try:
                        payload = part
                        # Fix padding properly
                        missing_padding = len(payload) % 4
                        if missing_padding:
                            payload += '=' * (4 - missing_padding)
                        decoded_bytes = base64.urlsafe_b64decode(payload)
                        decoded = json.loads(decoded_bytes.decode('utf-8'))
                        email = decoded.get('email') or decoded.get('sub')
                        if email:
                            print(f"Successfully decoded token using part {i} for user: {email}")
                            return email
                    except Exception as part_error:
                        print(f"Base64 decoding failed for part {i}: {part_error}")
                        continue
                
                return None
        else:
            print(f"Token doesn't have enough parts, has {len(parts)} parts")
            return None
        
    except Exception as e:
        print(f"Error decoding Auth0 token: {e}")
        return None

def get_user_email_from_request():
    """Get user email from the Authorization header (Auth0 tokens only)"""
    try:
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
            print(f"Received token: {token[:20]}...")  # Print first 20 chars for debugging
            return get_user_email_from_auth0_token(token)
    except Exception as e:
        print(f"Error getting user email from request: {e}")
    return None

def require_auth(f):
    """Custom decorator to require Auth0 authenticati   on"""
    def decorated_function(*args, **kwargs):
        # Check if Authorization header is present
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Authentication required'}), 401
        
        # For encrypted JWTs, we can't decode the payload, but we can verify the token exists
        # The frontend will send the user email in the request body for user-specific operations
        token = auth_header.split(' ')[1]
        if not token:
            return jsonify({'error': 'Authentication required'}), 401
        
        # Log the token for debugging
        print(f"[DEBUG] Auth token received: {token[:20]}...")
        
        # For now, just verify the token exists and has the right format
        # In production, you should verify the token signature with Auth0's public keys
        if len(token.split('.')) >= 3:  # Basic JWT format check
            return f(*args, **kwargs)
        else:
            return jsonify({'error': 'Invalid token format'}), 401
    
    decorated_function.__name__ = f.__name__
    return decorated_function

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "status": "healthy",
        "service": "ask-sai-baba-backend",
        "version": "1.0.0",
        "vector_store_healthy": vector_store_healthy
    })


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
            # Use Weaviate hybrid search directly
            from utils import weaviate_hybrid_search
            results = weaviate_hybrid_search(query, article_collection)
            return jsonify(results)
        else:
            return jsonify({'error': 'Query parameter is missing'}), 400
    else:
        return jsonify({'error': 'Request must contain JSON data'}), 400


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


# New endpoints for chat management
@app.route('/chats/<user_email>', methods=['GET'])
@require_auth
def get_user_chats(user_email):
    """Get all chat threads for the logged-in user"""
    try:
        # For encrypted JWT tokens, we can't decode them to get user email
        # So we'll use the user email from the URL path
        # The frontend is responsible for sending the correct user email
        request_user_email = user_email
        
        # Add some basic validation for the email format
        if not request_user_email or '@' not in request_user_email:
            return jsonify({'error': 'Invalid user email'}), 400
        
        # Find all chat threads for this user
        chat_threads = list(chat_collection.find(
            {'user_email': request_user_email},
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
@require_auth
def get_chat_thread(thread_id):
    """Get a specific chat thread with all its messages"""
    try:
        user_email = get_user_email_from_request()
        if not user_email:
            return jsonify({'error': 'Authentication required'}), 401
        
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


@app.route('/chats/<user_email>', methods=['POST'])
@require_auth
def create_chat_thread(user_email):
    """Create a new chat thread"""
    try:
        # Logging for debugging
        print(f"[DEBUG] Incoming chat creation request for user_email in URL: {user_email}")
        data = request.json
        print(f"[DEBUG] Request body: {data}")

        # Get user email from request body (frontend sends this)
        request_user_email = data.get('user_email') if data else None
        print(f"[DEBUG] User email from request body: {request_user_email}")

        # Verify the user is creating chats for themselves
        if not request_user_email:
            print(f"[ERROR] No user_email in request body")
            return jsonify({'error': 'User email required in request body'}), 400
            
        if request_user_email != user_email:
            print(f"[ERROR] Unauthorized access: URL email {user_email} != body email {request_user_email}")
            return jsonify({'error': 'Unauthorized access'}), 403
        
        if not data or 'title' not in data:
            print(f"[ERROR] Missing title in request body")
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
        print(f"[DEBUG] Chat thread created with id: {thread_data['id']}")
        
        return jsonify(thread_data), 201
    except Exception as e:
        print(f"[ERROR] Exception during chat creation: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/chats/<user_email>/<thread_id>/messages', methods=['POST'])
@require_auth
def add_message_to_thread(user_email, thread_id):
    """Add a new message to a chat thread"""
    try:
        request_user_email = get_user_email_from_request()
        if not request_user_email:
            return jsonify({'error': 'Authentication required'}), 401
        
        # Verify the user is accessing their own thread
        if request_user_email != user_email:
            return jsonify({'error': 'Unauthorized access'}), 403
        
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
@require_auth
def update_chat_thread(thread_id):
    """Update a chat thread (e.g., title, messages)"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'Request body is required'}), 400
        
        # Get user email from request body (frontend sends this)
        user_email = data.get('user_email')
        if not user_email:
            return jsonify({'error': 'User email required in request body'}), 400
        
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
@require_auth
def delete_chat_thread(thread_id):
    """Delete a chat thread"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'Request body is required'}), 400
        
        # Get user email from request body (frontend sends this)
        user_email = data.get('user_email')
        if not user_email:
            return jsonify({'error': 'User email required in request body'}), 400
        
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


# ============================================
# Saved Discourses Endpoints
# ============================================

@app.route('/saved-discourses/<user_email>', methods=['POST'])
@require_auth
def save_discourse(user_email):
    """Save a discourse for the logged-in user"""
    try:
        request_user_email = get_user_email_from_request()
        if not request_user_email:
            return jsonify({'error': 'Authentication required'}), 401
        
        # Verify the user is saving to their own collection
        if request_user_email != user_email:
            return jsonify({'error': 'Unauthorized access'}), 403
        
        data = request.json
        
        if not data or 'discourse' not in data:
            return jsonify({'error': 'Discourse data is required'}), 400
        
        discourse = data.get('discourse')
        
        # Validate discourse has required fields
        if not discourse.get('title') or not discourse.get('content'):
            return jsonify({'error': 'Discourse title and content are required'}), 400
        
        # Check if this discourse is already saved by this user (avoid duplicates)
        existing = saved_discourses_collection.find_one({
            'user_email': user_email,
            'discourse.title': discourse.get('title')
        })
        
        if existing:
            return jsonify({
                'message': 'Discourse already saved',
                'saved_discourse_id': str(existing['_id']),
                'already_exists': True
            }), 200
        
        # Create saved discourse document
        saved_discourse = {
            'user_email': user_email,
            'discourse': {
                'title': discourse.get('title'),
                'content': discourse.get('content'),
                'source_url': discourse.get('source_url', ''),
                'source_citation': discourse.get('source_citation', ''),
                'highlights': discourse.get('highlights', [])  # Add highlights support
            },
            'question_context': data.get('question_context', ''),
            'saved_at': datetime.now(),
            'tags': data.get('tags', []),
            'notes': data.get('notes', '')
        }
        
        # Insert into database
        result = saved_discourses_collection.insert_one(saved_discourse)
        
        return jsonify({
            'message': 'Discourse saved successfully',
            'saved_discourse_id': str(result.inserted_id)
        }), 201
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/saved-discourses/<user_email>', methods=['GET'])
@require_auth
def get_saved_discourses(user_email):
    """Get all saved discourses for the logged-in user"""
    try:
        request_user_email = get_user_email_from_request()
        if not request_user_email:
            return jsonify({'error': 'Authentication required'}), 401
        
        # Verify the user is accessing their own saved discourses
        if request_user_email != user_email:
            return jsonify({'error': 'Unauthorized access'}), 403
        
        # Find all saved discourses for this user
        saved_discourses = list(saved_discourses_collection.find(
            {'user_email': user_email}
        ).sort('saved_at', -1))  # Most recent first
        
        # Convert ObjectId to string for JSON serialization
        for discourse in saved_discourses:
            discourse['id'] = str(discourse['_id'])
            discourse['_id'] = str(discourse['_id'])
            discourse['saved_at'] = discourse['saved_at'].isoformat()
        
        return jsonify(saved_discourses), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/saved-discourses/<discourse_id>', methods=['DELETE'])
@require_auth
def delete_saved_discourse(discourse_id):
    """Delete a saved discourse"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'Request body is required'}), 400
        
        user_email = data.get('user_email')
        if not user_email:
            return jsonify({'error': 'User email required in request body'}), 400
        
        request_user_email = get_user_email_from_request()
        if not request_user_email or request_user_email != user_email:
            return jsonify({'error': 'Unauthorized access'}), 403
        
        # Verify the discourse belongs to the user and delete it
        result = saved_discourses_collection.delete_one({
            '_id': ObjectId(discourse_id),
            'user_email': user_email
        })
        
        if result.deleted_count == 0:
            return jsonify({'error': 'Saved discourse not found'}), 404
        
        return jsonify({'message': 'Saved discourse deleted successfully'}), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/saved-discourses/<discourse_id>', methods=['PUT'])
@require_auth
def update_saved_discourse(discourse_id):
    """Update a saved discourse (e.g., add/remove highlights)"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'Request body is required'}), 400
        
        user_email = data.get('user_email')
        if not user_email:
            return jsonify({'error': 'User email required'}), 400
        
        request_user_email = get_user_email_from_request()
        if not request_user_email or request_user_email != user_email:
            return jsonify({'error': 'Unauthorized access'}), 403
        
        # Prepare update data
        update_data = {}
        if 'highlights' in data:
            update_data['discourse.highlights'] = data['highlights']
        if 'notes' in data:
            update_data['notes'] = data['notes']
        if 'tags' in data:
            update_data['tags'] = data['tags']
        
        if not update_data:
            return jsonify({'error': 'No update data provided'}), 400
        
        # Update the document
        result = saved_discourses_collection.update_one(
            {
                '_id': ObjectId(discourse_id),
                'user_email': user_email
            },
            {'$set': update_data}
        )
        
        if result.matched_count == 0:
            return jsonify({'error': 'Saved discourse not found'}), 404
        
        return jsonify({'message': 'Saved discourse updated successfully'}), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/saved-discourses/check', methods=['POST'])
@require_auth
def check_discourse_saved(user_email):
    """Check if a discourse is already saved by the user"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'Request body is required'}), 400
        
        user_email = data.get('user_email')
        discourse_title = data.get('discourse_title')
        
        if not user_email or not discourse_title:
            return jsonify({'error': 'User email and discourse title are required'}), 400
        
        request_user_email = get_user_email_from_request()
        if not request_user_email or request_user_email != user_email:
            return jsonify({'error': 'Unauthorized access'}), 403
        
        # Check if discourse exists
        existing = saved_discourses_collection.find_one({
            'user_email': user_email,
            'discourse.title': discourse_title
        })
        
        if existing:
            return jsonify({
                'is_saved': True,
                'saved_discourse_id': str(existing['_id'])
            }), 200
        else:
            return jsonify({
                'is_saved': False,
                'saved_discourse_id': None
            }), 200
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500



@app.route('/conversation/clear', methods=['POST'])
def clear_conversation():
    """Clear conversation memory for a session."""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'Request must contain JSON data'}), 400

        session_id = data.get('session_id')
        user_id = data.get('user_id')
        
        if not session_id:
            return jsonify({'error': 'session_id is required'}), 400

        success = clear_conversation_memory(session_id, user_id)
        
        if success:
            return jsonify({'message': 'Conversation cleared successfully'}), 200
        else:
            return jsonify({'error': 'Failed to clear conversation'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/conversation/history', methods=['GET'])
def get_conversation_history():
    """Get conversation history for a session."""
    try:
        session_id = request.args.get('session_id')
        user_id = request.args.get('user_id')
        
        if not session_id:
            return jsonify({'error': 'session_id is required'}), 400

        # Get conversation from MongoDB
        conversation_doc = db.conversations.find_one({
            "session_id": session_id,
            "user_id": user_id
        })
        
        if conversation_doc:
            # Format messages for response
            messages = []
            for msg in conversation_doc.get("messages", []):
                messages.append({
                    "type": msg["type"],
                    "content": msg["content"],
                    "timestamp": msg["timestamp"].isoformat() if isinstance(msg["timestamp"], datetime) else str(msg["timestamp"])
                })
            
            return jsonify({
                'session_id': session_id,
                'user_id': user_id,
                'messages': messages,
                'last_updated': conversation_doc["last_updated"].isoformat() if isinstance(conversation_doc["last_updated"], datetime) else str(conversation_doc["last_updated"])
            }), 200
        else:
            return jsonify({
                'session_id': session_id,
                'user_id': user_id,
                'messages': [],
                'last_updated': None
            }), 200
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    


# register and login routes from # 9fae9b759d4511d1af3bd8338ee687ef09883d02
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

    if users_collection.find_one({'email': email}):
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
            users_collection.insert_one(user_data)

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
    user = users_collection.find_one({'email': email})
    if(user == None):
        return jsonify({'message': 'Incorrect email'}), 401
    
    if user and bcrypt.checkpw(password.encode('utf-8'), user['password']):
        access_token = create_access_token(identity=email)
        return jsonify({
            'access_token': access_token,
            'user': {
                'email': user['email'],
                'first_name': user['first_name'],
                'last_name': user['last_name']
            }
        }), 200
    else:
        return jsonify({'message': 'Invalid credentials'}), 401


@app.route('/auth/google/authorize', methods=['GET'])
def google_authorize():
    """Initiate Google OAuth flow - Step 1: Redirect user to Google"""
    try:
        google_client_id = os.getenv('GOOGLE_CLIENT_ID')
        google_client_secret = os.getenv('GOOGLE_CLIENT_SECRET')
        frontend_url = os.getenv('FRONTEND_URL', 'http://localhost:3000')
        
        if not google_client_id or not google_client_secret:
            return jsonify({'error': 'Google OAuth not configured'}), 500
        
        # Create OAuth2 flow
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": google_client_id,
                    "client_secret": google_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [f"{request.host_url}auth/google/callback"]
                }
            },
            scopes=['openid', 'https://www.googleapis.com/auth/userinfo.email', 'https://www.googleapis.com/auth/userinfo.profile']
        )
        
        flow.redirect_uri = f"{request.host_url}auth/google/callback"
        
        # Generate authorization URL
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true'
        )
        
        # Store state in session or database (for security)
        # For now, we'll pass it through the OAuth flow
        
        return redirect(authorization_url)
        
    except Exception as e:
        print(f"❌ Error in Google authorize: {e}")
        import traceback
        traceback.print_exc()
        return redirect(f"{frontend_url}/signin?error=oauth_failed")


@app.route('/auth/google/callback', methods=['GET'])
def google_callback():
    """Handle Google OAuth callback - Step 2: Exchange code for user info"""
    try:
        google_client_id = os.getenv('GOOGLE_CLIENT_ID')
        google_client_secret = os.getenv('GOOGLE_CLIENT_SECRET')
        frontend_url = os.getenv('FRONTEND_URL', 'http://localhost:3000')
        
        # Get authorization code from query params
        code = request.args.get('code')
        
        if not code:
            return redirect(f"{frontend_url}/signin?error=no_code")
        
        # Exchange code for tokens
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": google_client_id,
                    "client_secret": google_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [f"{request.host_url}auth/google/callback"]
                }
            },
            scopes=['openid', 'https://www.googleapis.com/auth/userinfo.email', 'https://www.googleapis.com/auth/userinfo.profile']
        )
        
        flow.redirect_uri = f"{request.host_url}auth/google/callback"
        
        # Fetch tokens
        flow.fetch_token(code=code)
        credentials = flow.credentials
        
        # Get user info from Google
        import requests as req
        userinfo_response = req.get(
            'https://www.googleapis.com/oauth2/v3/userinfo',
            headers={'Authorization': f'Bearer {credentials.token}'}
        )
        
        userinfo = userinfo_response.json()
        
        email = userinfo.get('email')
        given_name = userinfo.get('given_name', '')
        family_name = userinfo.get('family_name', '')
        picture = userinfo.get('picture', '')
        
        if not email:
            return redirect(f"{frontend_url}/signin?error=no_email")
        
        print(f"✅ Google OAuth successful for user: {email}")
        
        # Check if user exists in database
        user = users_collection.find_one({'email': email})
        
        if user:
            existing_provider = user.get('auth_provider', 'email')
            
            # Auto-link accounts if user signed up with email/password
            if existing_provider == 'email':
                users_collection.update_one(
                    {'email': email},
                    {
                        '$set': {
                            'auth_provider': 'both',
                            'google_profile_picture': picture,
                            'google_linked_at': datetime.now(),
                            'last_login': datetime.now()
                        }
                    }
                )
                print(f"🔗 Auto-linked Google account to existing email account: {email}")
            
            else:
                # Already using Google or both - just update last login
                users_collection.update_one(
                    {'email': email},
                    {'$set': {'last_login': datetime.now()}}
                )
                print(f"✅ Existing Google/both user logged in: {email}")
            
            # Get updated user data
            user = users_collection.find_one({'email': email})
        
        else:
            # New user - create with Google
            user_data = {
                'email': email,
                'first_name': given_name,
                'last_name': family_name,
                'profile_picture': picture,
                'auth_provider': 'google',
                'password': None,
                'created_at': datetime.now(),
                'last_login': datetime.now()
            }
            users_collection.insert_one(user_data)
            user = user_data
            print(f"✅ New Google user created: {email}")
        
        # Generate JWT token (same as regular login)
        access_token = create_access_token(identity=email)
        
        # Redirect to frontend with token
        # The frontend will extract the token from URL and store it
        user_data = {
            'email': user['email'],
            'first_name': user.get('first_name', ''),
            'last_name': user.get('last_name', ''),
            'profile_picture': user.get('profile_picture') or user.get('google_profile_picture', ''),
            'auth_provider': user.get('auth_provider', 'google')
        }
        
        # Encode user data for URL
        import urllib.parse
        user_json = json.dumps(user_data)
        user_encoded = urllib.parse.quote(user_json)
        
        return redirect(f"{frontend_url}/signin?token={access_token}&user={user_encoded}&success=true")
        
    except Exception as e:
        print(f"❌ Error in Google callback: {e}")
        import traceback
        traceback.print_exc()
        frontend_url = os.getenv('FRONTEND_URL', 'http://localhost:3000')
        return redirect(f"{frontend_url}/signin?error=oauth_failed")


@app.route('/auth/google/login', methods=['POST'])
def google_login():
    """Legacy endpoint - kept for backward compatibility"""
    return jsonify({'error': 'This endpoint is deprecated. Use /auth/google/authorize instead.'}), 400


# Password Reset Functions and Endpoints
def send_reset_email(email, token):
    """Send password reset email to user"""
    try:
        reset_url = f"{os.getenv('FRONTEND_URL', 'http://localhost:3000')}/password/newpassword?token={token}"
        
        msg = Message(
            subject='Password Reset Request - Ask Sai Vidya',
            recipients=[email],
            html=f"""
            <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                    <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                        <h2 style="color: #fb923c;">Password Reset Request</h2>
                        <p>Hello,</p>
                        <p>You have requested to reset your password for Ask Sai Vidya. Click the button below to reset your password:</p>
                        <div style="text-align: center; margin: 30px 0;">
                            <a href="{reset_url}" 
                               style="background-color: #fb923c; color: white; padding: 12px 30px; 
                                      text-decoration: none; border-radius: 5px; display: inline-block;">
                                Reset Password
                            </a>
                        </div>
                        <p>Or copy and paste this link into your browser:</p>
                        <p style="word-break: break-all; color: #666;">{reset_url}</p>
                        <p><strong>This link will expire in 1 hour.</strong></p>
                        <p>If you did not request a password reset, please ignore this email and your password will remain unchanged.</p>
                        <hr style="margin: 30px 0; border: none; border-top: 1px solid #ddd;">
                        <p style="color: #666; font-size: 12px;">
                            This is an automated message from Ask Sai Vidya. Please do not reply to this email.
                        </p>
                    </div>
                </body>
            </html>
            """
        )
        mail.send(msg)
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False


@app.route('/password/reset/request', methods=['POST'])
def request_password_reset():
    """Request a password reset token"""
    try:
        if request.is_json:
            email = request.json.get('email')
        else:
            email = request.form.get('email')
        
        if not email:
            return jsonify({'error': 'Email is required'}), 400
        
        # Check if user exists
        user = users_collection.find_one({'email': email})
        
        # Always return success to prevent email enumeration
        if not user:
            return jsonify({'message': 'If an account exists with this email, a password reset link has been sent.'}), 200
        
        # Generate secure token
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        
        # Set expiration time (1 hour from now)
        expires_at = datetime.now() + timedelta(hours=1)
        
        # Store reset token in database
        reset_data = {
            'user_email': email,
            'token_hash': token_hash,
            'created_at': datetime.now(),
            'expires_at': expires_at,
            'used': False
        }
        
        # Remove any existing unused tokens for this user
        db.password_resets.delete_many({'user_email': email, 'used': False})
        
        # Insert new token
        db.password_resets.insert_one(reset_data)
        
        # Send email
        email_sent = send_reset_email(email, token)
        
        if not email_sent:
            return jsonify({'error': 'Failed to send reset email. Please try again later.'}), 500
        
        return jsonify({'message': 'If an account exists with this email, a password reset link has been sent.'}), 200
        
    except Exception as e:
        print(f"Error in password reset request: {e}")
        return jsonify({'error': 'An error occurred. Please try again later.'}), 500


@app.route('/password/reset/verify', methods=['POST'])
def verify_reset_token():
    """Verify if a reset token is valid"""
    try:
        if request.is_json:
            token = request.json.get('token')
        else:
            token = request.form.get('token')
        
        if not token:
            return jsonify({'valid': False, 'error': 'Token is required'}), 400
        
        # Hash the token to compare with stored hash
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        
        # Find the token in database
        reset_record = db.password_resets.find_one({
            'token_hash': token_hash,
            'used': False
        })
        
        if not reset_record:
            return jsonify({'valid': False, 'error': 'Invalid or expired reset token'}), 400
        
        # Check if token has expired
        if datetime.now() > reset_record['expires_at']:
            return jsonify({'valid': False, 'error': 'Reset token has expired'}), 400
        
        return jsonify({
            'valid': True,
            'email': reset_record['user_email']
        }), 200
        
    except Exception as e:
        print(f"Error verifying token: {e}")
        return jsonify({'valid': False, 'error': 'An error occurred'}), 500


@app.route('/password/reset/confirm', methods=['POST'])
def confirm_password_reset():
    """Reset password with valid token"""
    try:
        if request.is_json:
            token = request.json.get('token')
            new_password = request.json.get('password')
        else:
            token = request.form.get('token')
            new_password = request.form.get('password')
        
        if not token or not new_password:
            return jsonify({'error': 'Token and new password are required'}), 400
        
        # Validate password strength
        if len(new_password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters long'}), 400
        
        # Hash the token
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        
        # Find the token
        reset_record = db.password_resets.find_one({
            'token_hash': token_hash,
            'used': False
        })
        
        if not reset_record:
            return jsonify({'error': 'Invalid or expired reset token'}), 400
        
        # Check if token has expired
        if datetime.now() > reset_record['expires_at']:
            return jsonify({'error': 'Reset token has expired'}), 400
        
        # Hash the new password
        hashed_password = bcrypt.hashpw(
            new_password.encode('utf-8'),
            bcrypt.gensalt()
        )
        
        # Update user's password
        result = users_collection.update_one(
            {'email': reset_record['user_email']},
            {'$set': {'password': hashed_password}}
        )
        
        if result.modified_count == 0:
            return jsonify({'error': 'Failed to update password'}), 500
        
        # Mark token as used
        db.password_resets.update_one(
            {'_id': reset_record['_id']},
            {'$set': {'used': True, 'used_at': datetime.now()}}
        )
        
        return jsonify({'message': 'Password has been reset successfully'}), 200
        
    except Exception as e:
        print(f"Error resetting password: {e}")
        return jsonify({'error': 'An error occurred. Please try again.'}), 500


# Configure the Flask app
port = int(os.getenv('PORT') or os.getenv('FLASK_RUN_PORT', 8000))
host = os.getenv('FLASK_RUN_HOST', '0.0.0.0')
debug = os.getenv('FLASK_DEBUG', '0') == '1'

if __name__ == "__main__":
    print(f"\n🚀 Starting Flask server on http://{host}:{port}")
    print("Press CTRL+C to quit\n")
    app.run(
        host=host,
        port=port,
        debug=debug
    )
