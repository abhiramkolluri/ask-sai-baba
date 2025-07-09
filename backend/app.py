from flask_cors import CORS
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import configparser
import os
from datetime import datetime
from bson import ObjectId
from generate_training import summarize_question as generate_summary
import jwt

from pymongo import MongoClient
from openai import OpenAI
from utils import handle_user_query, search_browse, get_full_article, model, clear_conversation_memory, check_vector_store_health

# Initialize Flask app
app = Flask(__name__)

# CORS configuration
cors = CORS(app)

# loading the env variables
load_dotenv()

# setting up open ai and mongodb
client = MongoClient(os.getenv("MONGO_URI"))

db = client.saibabasayings
article_collection = db.articles
chat_collection = db.chats  # New collection for storing chat threads

config = configparser.ConfigParser()

# setting up openai
config.read('openai.ini')

openai_client = OpenAI(api_key=config['OpenAI']['api_key'])

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
    """Custom decorator to require Auth0 authentication"""
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

        # Get optional session and user parameters for memory
        session_id = data.get('session_id')
        user_id = data.get('user_id')
        
        # Get user email from request body first, then fallback to token
        user_email = data.get('user_email')
        
        # If no email in body, try to get from token
        if not user_email:
            try:
                # Try to get user email from JWT token if Authorization header is present
                auth_header = request.headers.get('Authorization')
                if auth_header and auth_header.startswith('Bearer '):
                    token = auth_header.split(' ')[1]
                    user_email = get_user_email_from_auth0_token(token)
            except Exception:
                # If JWT validation fails, continue without user email
                pass

        # First get the search results using the same method as search endpoint
        query_embedding = model(query)
        search_results = search_browse(query_embedding, article_collection)
        
        # Call handle_user_query function with memory support and user email, passing the search results
        response = handle_user_query(
            query, 
            article_collection, 
            session_id=session_id, 
            user_id=user_id,
            user_email=user_email,
            search_results=search_results
        )
        
        return jsonify({
            'response': response,
            'session_id': session_id
        }), 200
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

@app.route('/summarize-question', methods=['POST'])
def summarize_question_endpoint():
    data = request.json
    if not data or 'question' not in data:
        return jsonify({'error': 'Question is required'}), 400
    
    summary = generate_summary(data['question'])
    return jsonify({'summary': summary}), 200


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


if __name__ == "__main__":
    app.run(debug=True, port=8000)
