from flask_cors import CORS
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import os
from datetime import datetime
from functools import wraps
import json

from openai import OpenAI
from weaviate_client import get_client, init_schema
from utils import (
    handle_user_query, 
    get_full_article, 
    clear_conversation_memory, 
    check_vector_store_health,
    search_browse,
    load_conversation_history
)

from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

# Initialize Flask app
app = Flask(__name__)

# loading the env variables
load_dotenv()

# Setup CORS
frontend_urls_raw = os.getenv("FRONTEND_URL", "http://localhost:3000,http://127.0.0.1:3000,https://asksaividya.com,https://develop.d1zpscp56yzv1s.amplifyapp.com")
frontend_urls = [url.strip() for url in frontend_urls_raw.split(',')]

cors = CORS(app, resources={
    r"/*": {
        "origins": frontend_urls,
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

# Initialize Weaviate schema and client
print("Initializing Weaviate schema...")
init_schema()
weaviate_client = get_client()

print("Checking vector store health...")
vector_store_healthy = check_vector_store_health()
if vector_store_healthy:
    print("✅ Vector store is healthy and ready for searches")
else:
    print("⚠️  Vector store health check failed - Weaviate may not be responsive.")

def verify_google_token(token):
    try:
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        # If client ID is missing but token is present, fallback mechanism for development.
        if not client_id:
            print("WARNING: GOOGLE_CLIENT_ID not set, authentication is bypassed natively.")
            import jwt
            decoded = jwt.decode(token, options={"verify_signature": False})
            return decoded
        idinfo = id_token.verify_oauth2_token(token, google_requests.Request(), client_id)
        return idinfo
    except Exception as e:
        print(f"Error verifying Google token: {e}")
        return None

def get_user_email_from_request():
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header.split(' ')[1]
        idinfo = verify_google_token(token)
        if idinfo:
            return idinfo.get('email')
    return None

def require_auth(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Authentication required'}), 401
        
        token = auth_header.split(' ')[1]
        if not token:
            return jsonify({'error': 'Authentication required'}), 401
            
        idinfo = verify_google_token(token)
        if not idinfo:
            return jsonify({'error': 'Invalid or expired token'}), 401
            
        return f(*args, **kwargs)
    return decorated_function

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "status": "healthy",
        "service": "ask-sai-baba-backend",
        "version": "1.0.0",
        "vector_store_healthy": check_vector_store_health()
    })

@app.route('/search', methods=['POST'])
def search_endpoint():
    if request.is_json:
        query = request.json.get('query')
        if query:
            results = search_browse(query)
            return jsonify(results)
        else:
            return jsonify({'error': 'Query parameter is missing'}), 400
    else:
        return jsonify({'error': 'Request must contain JSON data'}), 400

@app.route('/query', methods=['POST'])
def query_endpoint():
    if request.is_json:
        query = request.json.get('query')
        session_id = request.json.get('session_id')
        user_id = request.json.get('user_id')
        user_email = request.json.get('user_email')
        
        if not query:
            return jsonify({'error': 'Query parameter is required'}), 400
        
        try:
            search_results = search_browse(query)
            answer = handle_user_query(
                query=query,
                collection=None,
                session_id=session_id,
                user_id=user_id,
                user_email=user_email,
                search_results=search_results
            )
            
            return jsonify({
                'response': answer,
                'session_id': session_id
            }), 200
        except Exception as e:
            print(f"Error in query_endpoint: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'error': f'An error occurred: {str(e)}'}), 500
    else:
        return jsonify({'error': 'Request must contain JSON data'}), 400

@app.route('/summarize-question', methods=['POST'])
def summarize_question():
    if request.is_json:
        query = request.json.get('query')
        if not query:
            return jsonify({'error': 'Query parameter is required'}), 400
        try:
            from utils import openai_client, load_fine_tuned_model_id_from_file
            model_id = load_fine_tuned_model_id_from_file()
            response = openai_client.chat.completions.create(
                model=model_id,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant. Summarize the following question in 3 to 6 words to be used as a chat thread title."},
                    {"role": "user", "content": query}
                ]
            )
            summary = response.choices[0].message.content.strip('"')
            return jsonify({'summary': summary}), 200
        except Exception as e:
            print(f"Error summarizing: {e}")
            return jsonify({'error': str(e)}), 500
    else:
        return jsonify({'error': 'Request must contain JSON data'}), 400

@app.route('/blog/<id>', methods=['GET'])
def get_article(id):
    if id:
        article = get_full_article(id)
        if article:
            return jsonify(article)
        else:
            return jsonify({'error': 'Article not found'}), 404
    else:
        return jsonify({'error': 'ID parameter is missing'}), 400

# Saved Discourses Endpoints
@app.route('/saved-discourses/<user_email>', methods=['GET'])
@require_auth
def get_saved_discourses(user_email):
    try:
        request_user_email = get_user_email_from_request()
        if not request_user_email or request_user_email != user_email:
            return jsonify({'error': 'Unauthorized access'}), 403
            
        client = get_client()
        saved_col = client.collections.get("SavedDiscourse")
        from weaviate.classes.query import Filter, Sort
        
        response = saved_col.query.fetch_objects(
            filters=Filter.by_property("user_email").equal(user_email),
            sort=Sort.by_property("saved_at", ascending=False)
        )
        
        results = []
        for obj in response.objects:
            results.append({
                "id": str(obj.uuid),
                "article_uuid": obj.properties.get("article_uuid", ""),
                "title": obj.properties.get("title", ""),
                "content_preview": obj.properties.get("content_preview", ""),
                "link": obj.properties.get("link", ""),
                "collection_name": obj.properties.get("collection_name", ""),
                "saved_at": obj.properties.get("saved_at", datetime.now()).isoformat()
            })
            
        return jsonify(results), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/saved-discourses/<user_email>', methods=['POST'])
@require_auth
def create_saved_discourse(user_email):
    try:
        request_user_email = get_user_email_from_request()
        if not request_user_email or request_user_email != user_email:
            return jsonify({'error': 'Unauthorized access'}), 403
            
        data = request.json
        if not data or not data.get('article_uuid') or not data.get('title'):
            return jsonify({'error': 'article_uuid and title are required'}), 400
            
        client = get_client()
        saved_col = client.collections.get("SavedDiscourse")
        
        now = datetime.now()
        uuid = saved_col.data.insert(properties={
            "user_email": user_email,
            "article_uuid": data.get("article_uuid", ""),
            "title": data.get("title", ""),
            "content_preview": data.get("content_preview", ""),
            "link": data.get("link", ""),
            "collection_name": data.get("collection_name", ""),
            "saved_at": now
        })
        
        return jsonify({
            "id": str(uuid),
            "user_email": user_email,
            "article_uuid": data.get("article_uuid", ""),
            "title": data.get("title", ""),
            "content_preview": data.get("content_preview", ""),
            "link": data.get("link", ""),
            "collection_name": data.get("collection_name", ""),
            "saved_at": now.isoformat()
        }), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/saved-discourses/<user_email>/<discourse_id>', methods=['DELETE'])
@require_auth
def delete_saved_discourse(user_email, discourse_id):
    try:
        request_user_email = get_user_email_from_request()
        if not request_user_email or request_user_email != user_email:
            return jsonify({'error': 'Unauthorized access'}), 403
            
        client = get_client()
        saved_col = client.collections.get("SavedDiscourse")
        
        import uuid
        try:
            uuid_obj = uuid.UUID(discourse_id)
        except ValueError:
            return jsonify({'error': 'Invalid discourse ID'}), 400
            
        obj = saved_col.query.fetch_object_by_id(uuid_obj)
        if not obj or obj.properties.get("user_email") != user_email:
            return jsonify({'error': 'Saved discourse not found'}), 404
            
        saved_col.data.delete_by_id(uuid=uuid_obj)
        return jsonify({'message': 'Discourse removed successfully'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Chat Endpoints
@app.route('/chats/<user_email>', methods=['GET'])
@require_auth
def get_user_chats(user_email):
    try:
        request_user_email = user_email
        if not request_user_email or '@' not in request_user_email:
            return jsonify({'error': 'Invalid user email'}), 400
            
        client = get_client()
        chat_threads = client.collections.get("ChatThread")
        from weaviate.classes.query import Filter, Sort
        
        response = chat_threads.query.fetch_objects(
            filters=Filter.by_property("user_email").equal(request_user_email),
            sort=Sort.by_property("created_at", ascending=False)
        )
        
        results = []
        for obj in response.objects:
            thread = {
                "id": str(obj.uuid),
                "_id": str(obj.uuid),
                "title": obj.properties.get("title", ""),
                "timestamp": obj.properties.get("created_at", datetime.now()).isoformat()
            }
            messages_json = obj.properties.get("messages_json", "[]")
            thread["messages"] = json.loads(messages_json)
            results.append(thread)
            
        return jsonify(results), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/chats/<thread_id>', methods=['GET'])
@require_auth
def get_chat_thread(thread_id):
    try:
        user_email = get_user_email_from_request()
        if not user_email:
            return jsonify({'error': 'Authentication required'}), 401
            
        client = get_client()
        chat_threads = client.collections.get("ChatThread")
        
        import uuid
        try:
            uuid_obj = uuid.UUID(thread_id)
        except ValueError:
            return jsonify({'error': 'Invalid thread ID'}), 400
            
        obj = chat_threads.query.fetch_object_by_id(uuid_obj)
        if not obj or obj.properties.get("user_email") != user_email:
            return jsonify({'error': 'Chat thread not found'}), 404
            
        thread = {
            "id": str(obj.uuid),
            "_id": str(obj.uuid),
            "title": obj.properties.get("title", ""),
            "timestamp": obj.properties.get("created_at", datetime.now()).isoformat()
        }
        thread["messages"] = json.loads(obj.properties.get("messages_json", "[]"))
        
        return jsonify(thread), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/chats/<user_email>', methods=['POST'])
@require_auth
def create_chat_thread(user_email):
    try:
        data = request.json
        request_user_email = data.get('user_email') if data else None
        
        if not request_user_email:
            return jsonify({'error': 'User email required in request body'}), 400
            
        if request_user_email != user_email:
            return jsonify({'error': 'Unauthorized access'}), 403
            
        if not data or 'title' not in data:
            return jsonify({'error': 'Title is required'}), 400
            
        client = get_client()
        chat_threads = client.collections.get("ChatThread")
        
        now = datetime.now()
        uuid = chat_threads.data.insert(properties={
            "user_email": user_email,
            "title": data['title'],
            "created_at": now,
            "last_updated": now,
            "messages_json": "[]"
        })
        
        thread_data = {
            "id": str(uuid),
            "_id": str(uuid),
            "user_email": user_email,
            "title": data['title'],
            "timestamp": now.isoformat(),
            "messages": []
        }
        return jsonify(thread_data), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/chats/<user_email>/<thread_id>/messages', methods=['POST'])
@require_auth
def add_message_to_thread(user_email, thread_id):
    try:
        request_user_email = get_user_email_from_request()
        if not request_user_email:
            return jsonify({'error': 'Authentication required'}), 401
            
        if request_user_email != user_email:
            return jsonify({'error': 'Unauthorized access'}), 403
            
        data = request.json
        if not data or 'question' not in data or 'reply' not in data:
            return jsonify({'error': 'Question and reply are required'}), 400
            
        client = get_client()
        chat_threads = client.collections.get("ChatThread")
        
        import uuid
        uuid_obj = uuid.UUID(thread_id)
        obj = chat_threads.query.fetch_object_by_id(uuid_obj)
        
        if not obj or obj.properties.get("user_email") != user_email:
            return jsonify({'error': 'Chat thread not found'}), 404
            
        messages = json.loads(obj.properties.get("messages_json", "[]"))
        messages.append({
            'question': data['question'],
            'reply': data['reply'],
            'timestamp': datetime.now().isoformat()
        })
        
        chat_threads.data.update(
            uuid=uuid_obj,
            properties={
                "messages_json": json.dumps(messages),
                "last_updated": datetime.now()
            }
        )
        return jsonify({'message': 'Message added successfully'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/chats/<thread_id>', methods=['PUT'])
@require_auth
def update_chat_thread(thread_id):
    try:
        data = request.json
        user_email = data.get('user_email')
        if not user_email:
            return jsonify({'error': 'User email required'}), 400
            
        client = get_client()
        chat_threads = client.collections.get("ChatThread")
        
        import uuid
        uuid_obj = uuid.UUID(thread_id)
        obj = chat_threads.query.fetch_object_by_id(uuid_obj)
        
        if not obj or obj.properties.get("user_email") != user_email:
            return jsonify({'error': 'Chat thread not found'}), 404
            
        update_data = {"last_updated": datetime.now()}
        if 'title' in data:
            update_data['title'] = data['title']
        if 'messages' in data:
            update_data['messages_json'] = json.dumps(data['messages'])
            
        chat_threads.data.update(uuid=uuid_obj, properties=update_data)
        return jsonify({'message': 'Chat thread updated successfully'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/chats/<thread_id>', methods=['DELETE'])
@require_auth
def delete_chat_thread(thread_id):
    try:
        data = request.json
        user_email = data.get('user_email')
        
        if not user_email:
            return jsonify({'error': 'User email required'}), 400
            
        client = get_client()
        chat_threads = client.collections.get("ChatThread")
        
        import uuid
        uuid_obj = uuid.UUID(thread_id)
        obj = chat_threads.query.fetch_object_by_id(uuid_obj)
        
        if not obj or obj.properties.get("user_email") != user_email:
            return jsonify({'error': 'Chat thread not found'}), 404
            
        chat_threads.data.delete_by_id(uuid=uuid_obj)
        return jsonify({'message': 'Chat thread deleted successfully'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/conversation/clear', methods=['POST'])
def clear_conversation():
    try:
        data = request.json
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
    try:
        session_id = request.args.get('session_id')
        user_id = request.args.get('user_id')
        
        if not session_id:
            return jsonify({'error': 'session_id is required'}), 400

        client = get_client()
        conv_col = client.collections.get("Conversation")
        from weaviate.classes.query import Filter
        response = conv_col.query.fetch_objects(
            filters=Filter.by_property("session_id").equal(session_id),
            limit=1
        )
        
        if response.objects:
            obj = response.objects[0]
            messages_json = obj.properties.get("messages_json", "[]")
            messages = json.loads(messages_json)
            last_updated = obj.properties.get("last_updated", datetime.now()).isoformat()
            
            return jsonify({
                'session_id': session_id,
                'user_id': user_id,
                'messages': messages,
                'last_updated': last_updated
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

@app.route('/api/feedback', methods=['POST'])
def submit_feedback():
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'Request body is required'}), 400
            
        client = get_client()
        feedback_col = client.collections.get("Feedback")
        
        # Explicitly ignore citations per schema mismatch instruction, only log available parameters 
        feedback_col.data.insert(properties={
            "question": data.get("question", ""),
            "answer": data.get("answer", ""),
            "feedback_type": data.get("feedbackType", ""),
            "reason": data.get("reason", ""),
            "additional_comments": data.get("additionalComments", ""),
            "created_at": datetime.now()
        })
        
        return jsonify({'message': 'Feedback submitted successfully'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # When running directly, typically development mode
    port = int(os.environ.get('FLASK_RUN_PORT', 8000))
    host = os.environ.get('FLASK_RUN_HOST', '0.0.0.0')
    app.run(debug=True, host=host, port=port)
