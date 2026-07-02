#!/usr/bin/env python3
"""
Configure AWS API Gateway to reflect the current state of the backend.
This script creates/updates all resources and methods to match the Flask app endpoints.
"""

import boto3
import json
import sys
from botocore.exceptions import ClientError

# Configuration
API_GATEWAY_ID = 'dxhp0j33db'
BACKEND_URL = 'asv-dev.eba-hsdbwmfy.us-east-1.elasticbeanstalk.com'
STAGE_NAME = 'dev'

# Define all endpoints from app.py
ENDPOINTS = [
    # Health check
    {'path': '/', 'methods': ['GET']},
    
    # Search
    {'path': '/search', 'methods': ['POST']},
    
    # Blog/Article - Note: API Gateway has /blog/{id+} already, we'll use that
    {'path': '/blog/{id+}', 'methods': ['GET']},
    
    # Authentication
    {'path': '/register', 'methods': ['POST']},
    {'path': '/login', 'methods': ['POST']},
    
    # Google OAuth
    {'path': '/auth/google/authorize', 'methods': ['GET']},
    {'path': '/auth/google/callback', 'methods': ['GET']},
    {'path': '/auth/google/login', 'methods': ['POST']},
    
    # Password reset
    {'path': '/password/reset/request', 'methods': ['POST']},
    {'path': '/password/reset/verify', 'methods': ['POST']},
    {'path': '/password/reset/confirm', 'methods': ['POST']},
    
    # Chats
    # Note: API Gateway doesn't allow sibling resources with different path params
    # We'll need to handle /chats/{user_email} and /chats/{thread_id} separately
    # For now, we'll create /chats/{user_email} and handle thread_id via query param or different approach
    {'path': '/chats/{user_email}', 'methods': ['GET', 'POST']},
    {'path': '/chats/{user_email}/{thread_id}/messages', 'methods': ['POST']},
    
    # Saved discourses
    {'path': '/saved-discourses/{user_email}', 'methods': ['GET', 'POST']},
    {'path': '/saved-discourses/check', 'methods': ['POST']},
    {'path': '/saved-discourses/{user_email}/{discourse_id}', 'methods': ['DELETE']},
    
    # Conversation
    {'path': '/conversation/clear', 'methods': ['POST']},
    {'path': '/conversation/history', 'methods': ['GET']},
]

def get_or_create_resource(apigw, rest_api_id, parent_id, path_part):
    """Get existing resource or create a new one."""
    try:
        # Try to find existing resource
        resources = apigw.get_resources(restApiId=rest_api_id)
        for resource in resources['items']:
            if resource.get('pathPart') == path_part:
                # Check if it's under the correct parent
                if resource.get('parentId') == parent_id:
                    return resource['id']
        
        # Create new resource
        response = apigw.create_resource(
            restApiId=rest_api_id,
            parentId=parent_id,
            pathPart=path_part
        )
        return response['id']
    except ClientError as e:
        print(f"Error getting/creating resource {path_part}: {e}")
        return None

def create_resource_path(apigw, rest_api_id, root_id, path):
    """Create a resource path, creating intermediate resources as needed."""
    if path == '/':
        return root_id
    
    # Get all existing resources
    resources = apigw.get_resources(restApiId=rest_api_id)
    resource_map = {r['path']: r for r in resources['items']}
    
    # Check if path already exists
    if path in resource_map:
        return resource_map[path]['id']
    
    # Build path step by step
    parts = [p for p in path.split('/') if p]
    current_id = root_id
    current_path = '/'
    
    for part in parts:
        current_path = f"{current_path}{part}/" if current_path != '/' else f"/{part}"
        
        # Check if this path already exists
        if current_path in resource_map:
            current_id = resource_map[current_path]['id']
            continue
        
        # Handle path parameters like {id}
        if part.startswith('{') and part.endswith('}'):
            # Create path parameter resource
            try:
                response = apigw.create_resource(
                    restApiId=rest_api_id,
                    parentId=current_id,
                    pathPart=part  # Keep the braces
                )
                current_id = response['id']
                # Update resource map
                resource_map[current_path] = {'id': current_id, 'path': current_path}
            except ClientError as e:
                if e.response['Error']['Code'] == 'ConflictException':
                    # Resource already exists, find it
                    resources = apigw.get_resources(restApiId=rest_api_id)
                    for resource in resources['items']:
                        if resource.get('parentId') == current_id and resource.get('pathPart') == part:
                            current_id = resource['id']
                            break
                else:
                    print(f"Error creating path parameter resource {part}: {e}")
                    return None
        else:
            current_id = get_or_create_resource(apigw, rest_api_id, current_id, part)
            if current_id is None:
                return None
            # Update resource map
            resource_map[current_path] = {'id': current_id, 'path': current_path}
    
    return current_id

def setup_cors(apigw, rest_api_id, resource_id):
    """Set up CORS for a resource."""
    try:
        # Check if OPTIONS method already exists
        methods = apigw.get_resource(restApiId=rest_api_id, resourceId=resource_id)
        has_options = any(m == 'OPTIONS' for m in methods.get('resourceMethods', {}).keys())
        
        if not has_options:
            # Create OPTIONS method
            apigw.put_method(
                restApiId=rest_api_id,
                resourceId=resource_id,
                httpMethod='OPTIONS',
                authorizationType='NONE'
            )
            
            # Set up method response
            apigw.put_method_response(
                restApiId=rest_api_id,
                resourceId=resource_id,
                httpMethod='OPTIONS',
                statusCode='200',
                responseParameters={
                    'method.response.header.Access-Control-Allow-Origin': True,
                    'method.response.header.Access-Control-Allow-Headers': True,
                    'method.response.header.Access-Control-Allow-Methods': True
                }
            )
            
            # Set up integration
            apigw.put_integration(
                restApiId=rest_api_id,
                resourceId=resource_id,
                httpMethod='OPTIONS',
                type='MOCK',
                requestTemplates={
                    'application/json': '{"statusCode": 200}'
                }
            )
            
            # Set up integration response
            apigw.put_integration_response(
                restApiId=rest_api_id,
                resourceId=resource_id,
                httpMethod='OPTIONS',
                statusCode='200',
                responseParameters={
                    'method.response.header.Access-Control-Allow-Origin': "'*'",
                    'method.response.header.Access-Control-Allow-Headers': "'Content-Type,Authorization,X-Amz-Date,X-Api-Key,X-Amz-Security-Token'",
                    'method.response.header.Access-Control-Allow-Methods': "'GET,POST,PUT,DELETE,OPTIONS'"
                },
                responseTemplates={
                    'application/json': ''
                }
            )
    except ClientError as e:
        if e.response['Error']['Code'] != 'ConflictException':
            print(f"Error setting up CORS: {e}")

def create_method(apigw, rest_api_id, resource_id, method, path):
    """Create or update an HTTP method."""
    try:
        # Check if method already exists
        method_exists = False
        try:
            existing_method = apigw.get_method(restApiId=rest_api_id, resourceId=resource_id, httpMethod=method)
            method_exists = True
            print(f"  Method {method} already exists for {path}, updating...")
        except ClientError:
            print(f"  Creating method {method} for {path}...")
        
        # Prepare path parameters
        path_params = {}
        if '{id+}' in path:
            path_params['method.request.path.id'] = True
        elif '{id}' in path:
            path_params['method.request.path.id'] = True
        if '{user_email}' in path:
            path_params['method.request.path.user_email'] = True
        if '{thread_id}' in path:
            path_params['method.request.path.thread_id'] = True
        if '{discourse_id}' in path:
            path_params['method.request.path.discourse_id'] = True
        
        # Delete existing method if it exists to recreate with proper parameters
        if method_exists:
            try:
                apigw.delete_method(restApiId=rest_api_id, resourceId=resource_id, httpMethod=method)
            except:
                pass
        
        # Create method with path parameters
        if path_params:
            apigw.put_method(
                restApiId=rest_api_id,
                resourceId=resource_id,
                httpMethod=method,
                authorizationType='NONE',
                requestParameters=path_params
            )
        else:
            apigw.put_method(
                restApiId=rest_api_id,
                resourceId=resource_id,
                httpMethod=method,
                authorizationType='NONE'
            )
        
        # Set up integration
        # For HTTP_PROXY, keep {param} placeholders in the URI and map via requestParameters
        integration_path = path
        request_params = {}

        # Map path parameters for integration
        if '{id+}' in path:
            request_params['integration.request.path.id'] = 'method.request.path.id'
        elif '{id}' in path:
            request_params['integration.request.path.id'] = 'method.request.path.id'
        if '{user_email}' in path:
            request_params['integration.request.path.user_email'] = 'method.request.path.user_email'
        if '{thread_id}' in path:
            request_params['integration.request.path.thread_id'] = 'method.request.path.thread_id'
        if '{discourse_id}' in path:
            request_params['integration.request.path.discourse_id'] = 'method.request.path.discourse_id'

        integration_uri = f'http://{BACKEND_URL}{integration_path}'
        
        # HTTP_PROXY forwards the entire request (headers, body, query params) to the backend
        # without transformation — this is required for POST/PUT requests with a JSON body
        apigw.put_integration(
            restApiId=rest_api_id,
            resourceId=resource_id,
            httpMethod=method,
            type='HTTP_PROXY',
            integrationHttpMethod=method,
            uri=integration_uri,
            requestParameters=request_params if request_params else {}
        )

        # Method response (documentation only for HTTP_PROXY — status codes pass through automatically)
        try:
            apigw.put_method_response(
                restApiId=rest_api_id,
                resourceId=resource_id,
                httpMethod=method,
                statusCode='200',
                responseParameters={
                    'method.response.header.Access-Control-Allow-Origin': True
                }
            )
        except ClientError:
            pass  # May already exist
        
        
        print(f"    ✓ {method} method configured")
        
    except ClientError as e:
        print(f"    ✗ Error creating method {method}: {e}")

def main():
    print(f"🚀 Configuring API Gateway {API_GATEWAY_ID} for backend at {BACKEND_URL}\n")
    
    # Initialize API Gateway client
    apigw = boto3.client('apigateway')
    
    # Get root resource
    try:
        resources = apigw.get_resources(restApiId=API_GATEWAY_ID)
        root_id = None
        for resource in resources['items']:
            if resource['path'] == '/':
                root_id = resource['id']
                break
        
        if not root_id:
            print("❌ Could not find root resource")
            sys.exit(1)
        
        print(f"✓ Found root resource: {root_id}\n")
        
        # Process each endpoint
        for endpoint in ENDPOINTS:
            path = endpoint['path']
            methods = endpoint['methods'] if isinstance(endpoint['methods'], list) else [endpoint['methods']]
            
            print(f"📝 Processing {path}...")
            
            # Create resource path
            resource_id = create_resource_path(apigw, API_GATEWAY_ID, root_id, path)
            if not resource_id:
                print(f"  ✗ Failed to create resource path")
                continue
            
            # Set up CORS
            setup_cors(apigw, API_GATEWAY_ID, resource_id)
            
            # Create methods
            for method in methods:
                create_method(apigw, API_GATEWAY_ID, resource_id, method, path)
            
            print(f"  ✓ {path} configured\n")
        
        # Deploy API
        print("🚀 Deploying API...")
        try:
            apigw.create_deployment(
                restApiId=API_GATEWAY_ID,
                stageName=STAGE_NAME,
                description='Updated to match current backend state'
            )
            print(f"✓ API deployed to {STAGE_NAME} stage")
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConflictException':
                # Update existing deployment
                apigw.create_deployment(
                    restApiId=API_GATEWAY_ID,
                    stageName=STAGE_NAME,
                    description='Updated to match current backend state'
                )
                print(f"✓ API updated and deployed to {STAGE_NAME} stage")
            else:
                print(f"✗ Error deploying API: {e}")
        
        # Get the API endpoint URL
        try:
            api = apigw.get_rest_api(restApiId=API_GATEWAY_ID)
            account_id = boto3.client('sts').get_caller_identity()['Account']
            region = apigw.meta.region_name
            api_url = f"https://{API_GATEWAY_ID}.execute-api.{region}.amazonaws.com/{STAGE_NAME}"
            print(f"\n✅ API Gateway configured successfully!")
            print(f"   API URL: {api_url}")
        except Exception as e:
            print(f"\n✅ API Gateway configured (could not get URL: {e})")
        
    except ClientError as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()

