from flask_cors import CORS
from flask import Flask, request, jsonify, redirect
from flask_mail import Mail, Message
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta, timezone
from functools import wraps
import json
from urllib.parse import urlencode, quote
import bcrypt
import jwt
import secrets
import hashlib

import requests

from openai import OpenAI
from weaviate_client import get_client, init_schema
from utils import (
    handle_user_query,
    get_full_article,
    clear_conversation_memory,
    check_vector_store_health,
    search_browse,
    load_conversation_history,
    extract_quoted_phrase
)

from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

# Initialize Flask app
app = Flask(__name__)

# loading the env variables
load_dotenv()

app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'ask-sai-dev-secret-change-this')
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True').lower() == 'true'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER', os.getenv('MAIL_USERNAME'))

mail = Mail(app)

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
        if "Token expired" not in str(e):
            print(f"Error verifying Google token: {e}")
        return None

def verify_manual_token(token):
    try:
        decoded = jwt.decode(token, app.config['JWT_SECRET_KEY'], algorithms=['HS256'])
        if decoded.get('token_type') != 'manual':
            return None
        return decoded
    except Exception:
        return None

def get_verified_identity(token):
    manual_identity = verify_manual_token(token)
    if manual_identity and manual_identity.get('email'):
        return {'email': manual_identity.get('email'), 'provider': 'manual'}

    google_identity = verify_google_token(token)
    if google_identity and google_identity.get('email'):
        return {'email': google_identity.get('email'), 'provider': 'google'}

    return None

def get_user_email_from_request():
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header.split(' ')[1]
        identity = get_verified_identity(token)
        if identity:
            return identity.get('email')
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

        identity = get_verified_identity(token)
        if not identity:
            return jsonify({'error': 'Invalid or expired token'}), 401
            
        return f(*args, **kwargs)
    return decorated_function

def get_frontend_password_reset_url(token):
    explicit_reset_url = os.getenv("FRONTEND_PASSWORD_RESET_URL")
    if explicit_reset_url:
        separator = '&' if '?' in explicit_reset_url else '?'
        return f"{explicit_reset_url}{separator}token={quote(token)}"

    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000").split(",")[0].strip()
    return f"{frontend_url.rstrip('/')}/password/newpassword?token={quote(token)}"

def send_reset_email(email, token):
    if not app.config.get('MAIL_USERNAME') or not app.config.get('MAIL_PASSWORD'):
        print("Password reset email config missing: MAIL_USERNAME/MAIL_PASSWORD")
        return False

    try:
        reset_url = get_frontend_password_reset_url(token)
        msg = Message(
            subject='Password Reset Request - Ask Sai Vidya',
            recipients=[email],
            html=f"""
            <html>
                <body style=\"font-family: Arial, sans-serif; line-height: 1.6; color: #333;\">
                    <div style=\"max-width: 600px; margin: 0 auto; padding: 20px;\">
                        <h2 style=\"color: #fb923c;\">Password Reset Request</h2>
                        <p>Hello,</p>
                        <p>You requested a password reset for Ask Sai Vidya.</p>
                        <p>
                            <a href=\"{reset_url}\" style=\"background-color: #fb923c; color: white; padding: 10px 16px; text-decoration: none; border-radius: 4px;\">
                                Reset Password
                            </a>
                        </p>
                        <p>This link will expire in 1 hour.</p>
                    </div>
                </body>
            </html>
            """
        )
        mail.send(msg)
        return True
    except Exception as e:
        print(f"Error sending password reset email: {e}")
        return False

def _parse_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except Exception:
            return None
    return None

def _is_token_used(value):
    return str(value).strip().lower() in {"true", "1", "yes"}

def _is_token_expired(expires_at):
    if not expires_at:
        return False

    if expires_at.tzinfo is not None:
        now = datetime.now(timezone.utc)
        return now > expires_at.astimezone(timezone.utc)

    return datetime.now() > expires_at

def get_frontend_signin_url():
    """Return the frontend signin URL used for OAuth callback redirection."""
    frontend_signin_url = os.getenv("FRONTEND_SIGNIN_URL")
    if frontend_signin_url:
        return frontend_signin_url

    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000").split(",")[0].strip()
    return f"{frontend_url.rstrip('/')}/signin"

def get_google_redirect_uri():
    """Return backend callback URI registered in Google Console."""
    explicit_redirect = os.getenv("GOOGLE_REDIRECT_URI")
    if explicit_redirect:
        return explicit_redirect

    backend_base = os.getenv("BACKEND_BASE_URL")
    if backend_base:
        return f"{backend_base.rstrip('/')}/auth/google/callback"

    return "http://localhost:8000/auth/google/callback"

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "status": "healthy",
        "service": "ask-sai-baba-backend",
        "version": "1.0.0",
        "vector_store_healthy": check_vector_store_health()
    })

@app.route('/auth/google/authorize', methods=['GET'])
def google_authorize():
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    redirect_uri = get_google_redirect_uri()

    if not client_id:
        return jsonify({"error": "Google OAuth is not configured (missing GOOGLE_CLIENT_ID)"}), 500

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }

    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    return redirect(auth_url)

@app.route('/auth/google/callback', methods=['GET'])
def google_callback():
    code = request.args.get('code')
    error = request.args.get('error')
    frontend_signin = get_frontend_signin_url()

    if error:
        return redirect(f"{frontend_signin}?error=oauth_failed")

    if not code:
        return redirect(f"{frontend_signin}?error=no_code")

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = get_google_redirect_uri()

    if not client_id or not client_secret:
        return redirect(f"{frontend_signin}?error=oauth_failed")

    token_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )

    if token_resp.status_code != 200:
        return redirect(f"{frontend_signin}?error=oauth_failed")

    token_data = token_resp.json()
    google_id_token = token_data.get("id_token")

    if not google_id_token:
        return redirect(f"{frontend_signin}?error=oauth_failed")

    idinfo = verify_google_token(google_id_token)
    if not idinfo:
        return redirect(f"{frontend_signin}?error=oauth_failed")

    user = {
        "email": idinfo.get("email"),
        "name": idinfo.get("name", ""),
        "first_name": idinfo.get("given_name", ""),
        "last_name": idinfo.get("family_name", ""),
        "picture": idinfo.get("picture", ""),
        "auth_provider": "google",
    }

    user_encoded = quote(json.dumps(user))
    if not user.get("email"):
        return redirect(f"{frontend_signin}?error=no_email")

    redirect_url = f"{frontend_signin}?success=true&token={quote(google_id_token)}&user={user_encoded}"
    return redirect(redirect_url)

@app.route('/auth/google/login', methods=['POST'])
def google_login():
    if not request.is_json:
        return jsonify({'error': 'Request must contain JSON data'}), 400

    token = request.json.get('token')
    if not token:
        return jsonify({'error': 'Google token is required'}), 400

    idinfo = verify_google_token(token)
    if not idinfo:
        return jsonify({'error': 'Invalid or expired Google token'}), 401

    user = {
        "email": idinfo.get("email"),
        "name": idinfo.get("name", ""),
        "first_name": idinfo.get("given_name", ""),
        "last_name": idinfo.get("family_name", ""),
        "picture": idinfo.get("picture", ""),
        "auth_provider": "google",
    }

    return jsonify({
        "token": token,
        "user": user,
        "message": "Google login successful"
    }), 200

@app.route('/register', methods=['POST'])
def register():
    first_name = request.json.get('first_name') if request.is_json else request.form.get('first_name')
    last_name = request.json.get('last_name') if request.is_json else request.form.get('last_name')
    email = request.json.get('email') if request.is_json else request.form.get('email')
    password = request.json.get('password') if request.is_json else request.form.get('password')

    if not first_name:
        return jsonify({'error': 'First Name is required'}), 400
    if not last_name:
        return jsonify({'error': 'Last Name is required'}), 400
    if not email:
        return jsonify({'error': 'Email is required'}), 400
    if not password:
        return jsonify({'error': 'Password is required'}), 400

    try:
        from weaviate.classes.query import Filter

        client = get_client()
        users_col = client.collections.get("UserAccount")

        existing = users_col.query.fetch_objects(
            filters=Filter.by_property("email").equal(email),
            limit=1
        )
        if existing.objects:
            return jsonify({'message': 'User already exists'}), 409

        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        users_col.data.insert(properties={
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "password_hash": password_hash,
            "auth_provider": "manual",
            "created_at": datetime.now()
        })

        return jsonify({'message': 'User registered successfully'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/login', methods=['POST'])
def login():
    email = request.json.get('email') if request.is_json else request.form.get('email')
    password = request.json.get('password') if request.is_json else request.form.get('password')

    if not email or not password:
        return jsonify({'message': 'Email and password are required'}), 400

    try:
        from weaviate.classes.query import Filter

        client = get_client()
        users_col = client.collections.get("UserAccount")
        response = users_col.query.fetch_objects(
            filters=Filter.by_property("email").equal(email),
            limit=1
        )

        if not response.objects:
            return jsonify({'message': 'Invalid email or password'}), 401

        user_obj = response.objects[0]
        stored_hash = user_obj.properties.get('password_hash')
        if not stored_hash or not bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8')):
            return jsonify({'message': 'Invalid email or password'}), 401

        token = jwt.encode({
            'email': email,
            'token_type': 'manual',
            'exp': datetime.utcnow() + timedelta(days=7)
        }, app.config['JWT_SECRET_KEY'], algorithm='HS256')

        return jsonify({
            'access_token': token,
            'user': {
                'email': email,
                'first_name': user_obj.properties.get('first_name', ''),
                'last_name': user_obj.properties.get('last_name', '')
            }
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/password/reset/request', methods=['POST'])
def request_password_reset():
    email = request.json.get('email') if request.is_json else request.form.get('email')
    if not email:
        return jsonify({'error': 'Email is required'}), 400

    try:
        from weaviate.classes.query import Filter

        client = get_client()
        users_col = client.collections.get("UserAccount")
        resets_col = client.collections.get("PasswordResetToken")

        user_lookup = users_col.query.fetch_objects(
            filters=Filter.by_property("email").equal(email),
            limit=1
        )

        if not user_lookup.objects:
            return jsonify({'message': 'If an account exists with this email, a password reset link has been sent.'}), 200

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now_utc = datetime.now(timezone.utc)
        expires_at = now_utc + timedelta(hours=1)

        resets_col.data.insert(properties={
            "user_email": email,
            "token_hash": token_hash,
            "created_at": now_utc,
            "expires_at": expires_at,
            "used": "false"
        })

        if not send_reset_email(email, token):
            return jsonify({'error': 'Failed to send reset email. Please verify mail configuration.'}), 500

        return jsonify({'message': 'If an account exists with this email, a password reset link has been sent.'}), 200
    except Exception as e:
        print(f"Error in password reset request: {e}")
        return jsonify({'error': 'An error occurred. Please try again later.'}), 500

@app.route('/password/reset/verify', methods=['POST'])
def verify_reset_token():
    token = request.json.get('token') if request.is_json else request.form.get('token')
    if not token:
        return jsonify({'valid': False, 'error': 'Token is required'}), 400

    try:
        from weaviate.classes.query import Filter

        token_hash = hashlib.sha256(token.encode()).hexdigest()
        client = get_client()
        resets_col = client.collections.get("PasswordResetToken")
        response = resets_col.query.fetch_objects(
            filters=Filter.by_property("token_hash").equal(token_hash),
            limit=1
        )

        if not response.objects:
            return jsonify({'valid': False, 'error': 'Invalid or expired reset token'}), 400

        reset_obj = response.objects[0]
        if _is_token_used(reset_obj.properties.get('used', 'false')):
            return jsonify({'valid': False, 'error': 'Invalid or expired reset token'}), 400

        expires_at = _parse_datetime(reset_obj.properties.get('expires_at'))
        if not expires_at:
            created_at = _parse_datetime(reset_obj.properties.get('created_at'))
            if created_at:
                expires_at = created_at + timedelta(hours=1)

        if _is_token_expired(expires_at):
            return jsonify({'valid': False, 'error': 'Reset token has expired'}), 400

        return jsonify({'valid': True, 'email': reset_obj.properties.get('user_email')}), 200
    except Exception as e:
        print(f"Error verifying reset token: {e}")
        return jsonify({'valid': False, 'error': 'An error occurred'}), 500

@app.route('/password/reset/confirm', methods=['POST'])
def confirm_password_reset():
    token = request.json.get('token') if request.is_json else request.form.get('token')
    new_password = request.json.get('password') if request.is_json else request.form.get('password')

    if not token or not new_password:
        return jsonify({'error': 'Token and new password are required'}), 400
    if len(new_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters long'}), 400

    try:
        from weaviate.classes.query import Filter

        token_hash = hashlib.sha256(token.encode()).hexdigest()
        client = get_client()
        resets_col = client.collections.get("PasswordResetToken")
        users_col = client.collections.get("UserAccount")

        reset_response = resets_col.query.fetch_objects(
            filters=Filter.by_property("token_hash").equal(token_hash),
            limit=1
        )
        if not reset_response.objects:
            return jsonify({'error': 'Invalid or expired reset token'}), 400

        reset_obj = reset_response.objects[0]
        if _is_token_used(reset_obj.properties.get('used', 'false')):
            return jsonify({'error': 'Invalid or expired reset token'}), 400

        expires_at = _parse_datetime(reset_obj.properties.get('expires_at'))
        if not expires_at:
            created_at = _parse_datetime(reset_obj.properties.get('created_at'))
            if created_at:
                expires_at = created_at + timedelta(hours=1)

        if _is_token_expired(expires_at):
            return jsonify({'error': 'Reset token has expired'}), 400

        user_email = reset_obj.properties.get('user_email')
        user_response = users_col.query.fetch_objects(
            filters=Filter.by_property("email").equal(user_email),
            limit=1
        )
        if not user_response.objects:
            return jsonify({'error': 'User account not found'}), 404

        user_obj = user_response.objects[0]
        password_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        users_col.data.update(uuid=user_obj.uuid, properties={'password_hash': password_hash})
        resets_col.data.update(
            uuid=reset_obj.uuid,
            properties={'used': 'true', 'used_at': datetime.now(timezone.utc)}
        )

        return jsonify({'message': 'Password has been reset successfully'}), 200
    except Exception as e:
        print(f"Error resetting password: {e}")
        return jsonify({'error': 'An error occurred. Please try again.'}), 500

@app.route('/search', methods=['POST'])
def search_endpoint():
    if request.is_json:
        query = request.json.get('query')
        if query:
            exact_phrase = extract_quoted_phrase(query)
            results = search_browse(query, exact_phrase=exact_phrase)
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
            exact_phrase = extract_quoted_phrase(query)
            search_results = search_browse(query, exact_phrase=exact_phrase)
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
    debug_enabled = os.environ.get('FLASK_DEBUG', '0').lower() in ('1', 'true', 'yes')
    app.run(debug=debug_enabled, host=host, port=port)
