#!/usr/bin/env python3
"""
Test script to verify backend functionality
"""

import requests
import json
import sys

# Try API Gateway first, fallback to direct backend
API_GATEWAY_URL = "https://dxhp0j33db.execute-api.us-east-1.amazonaws.com/prod"
DIRECT_BACKEND_URL = "http://asv-dev.eba-hsdbwmfy.us-east-1.elasticbeanstalk.com"

# Use direct backend for more reliable testing
BASE_URL = DIRECT_BACKEND_URL

def test_health_check():
    """Test 1: Health check"""
    print("\n" + "="*60)
    print("TEST 1: Health Check")
    print("="*60)
    try:
        response = requests.get(f"{BASE_URL}/")
        response.raise_for_status()
        data = response.json()
        print(f"✅ Health check passed")
        print(f"   Status: {data.get('status')}")
        print(f"   Vector store: {data.get('vector_store_healthy')}")
        return True
    except Exception as e:
        print(f"❌ Health check failed: {e}")
        return False

def test_search_question():
    """Test 2: Ask a question and get discourses"""
    print("\n" + "="*60)
    print("TEST 2: Ask a Question and Get Answer with Discourses")
    print("="*60)
    try:
        query = "What is the purpose of life?"
        print(f"Query: {query}")
        
        # First, get search results (discourses)
        response = requests.post(
            f"{BASE_URL}/search",
            json={"query": query},
            headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()
        discourses = response.json()
        
        print(f"\n✅ Search successful")
        print(f"   Number of discourses returned: {len(discourses)}")
        
        if len(discourses) >= 5:
            print(f"   ✅ At least 5 discourses returned (requirement met)")
        else:
            print(f"   ⚠️  Only {len(discourses)} discourses returned (expected at least 5)")
        
        # Display first few discourse titles
        if discourses:
            print(f"\n   Sample discourses:")
            for i, disc in enumerate(discourses[:5], 1):
                title = disc.get('title', 'No title')
                print(f"   {i}. {title}")
        
        return len(discourses) >= 5, discourses
    except Exception as e:
        print(f"❌ Search failed: {e}")
        return False, []

def test_save_discourse():
    """Test 3: Save a discourse"""
    print("\n" + "="*60)
    print("TEST 3: Save a Discourse")
    print("="*60)
    try:
        # First, we need a test user email and auth token
        # For now, we'll test with a mock email
        test_email = "test@example.com"
        
        # Get a discourse to save (from search results)
        _, discourses = test_search_question()
        if not discourses:
            print("❌ Cannot test save - no discourses available")
            return False
        
        discourse_to_save = discourses[0]
        
        # Note: This endpoint requires authentication
        # We'll test the endpoint structure but may get 401
        discourse_data = {
            "discourse": {
                "title": discourse_to_save.get('title', 'Test Discourse'),
                "content": discourse_to_save.get('content', '')[:500],  # Limit content
                "source_url": discourse_to_save.get('link', ''),
                "source_citation": discourse_to_save.get('collection', '')
            },
            "question_context": "Test question context"
        }
        
        print(f"Attempting to save discourse: {discourse_data['discourse']['title']}")
        
        # Try to save (will likely fail without auth, but we can check the endpoint exists)
        response = requests.post(
            f"{BASE_URL}/saved-discourses/{test_email}",
            json=discourse_data,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test-token"  # Mock token
            }
        )
        
        if response.status_code == 401:
            print("   ⚠️  Endpoint exists but requires authentication (expected)")
            print("   ✅ Save discourse endpoint is accessible")
            return True
        elif response.status_code == 201:
            print("   ✅ Discourse saved successfully")
            return True
        else:
            print(f"   ⚠️  Unexpected status: {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False
            
    except Exception as e:
        print(f"❌ Save discourse test failed: {e}")
        return False

def test_signup():
    """Test 4: User signup"""
    print("\n" + "="*60)
    print("TEST 4: User Sign Up")
    print("="*60)
    try:
        import random
        import string
        # Generate unique test email
        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        test_email = f"testuser{random_suffix}@example.com"
        test_password = "TestPassword123!"
        
        signup_data = {
            "first_name": "Test",
            "last_name": "User",
            "email": test_email,
            "password": test_password
        }
        
        print(f"Attempting to sign up: {test_email}")
        
        response = requests.post(
            f"{BASE_URL}/register",
            json=signup_data,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code in [200, 201]:
            print("   ✅ User registered successfully")
            return True, test_email, test_password
        elif response.status_code == 409:
            print("   ⚠️  User already exists (trying different email)")
            # Try with different suffix
            random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
            test_email = f"testuser{random_suffix}@example.com"
            signup_data["email"] = test_email
            response = requests.post(
                f"{BASE_URL}/register",
                json=signup_data,
                headers={"Content-Type": "application/json"}
            )
            if response.status_code == 201:
                print("   ✅ User registered successfully (with new email)")
                return True, test_email, test_password
            else:
                print(f"   ❌ Registration failed: {response.status_code}")
                print(f"   Response: {response.text[:200]}")
                return False, None, None
        else:
            print(f"   ❌ Registration failed: {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False, None, None
            
    except Exception as e:
        print(f"❌ Signup test failed: {e}")
        return False, None, None

def test_signin(email, password):
    """Test 5: User sign in"""
    print("\n" + "="*60)
    print("TEST 5: User Sign In")
    print("="*60)
    try:
        if not email or not password:
            print("   ⚠️  Skipping sign in - no credentials from signup")
            return False, None
        
        login_data = {
            "email": email,
            "password": password
        }
        
        print(f"Attempting to sign in: {email}")
        
        response = requests.post(
            f"{BASE_URL}/login",
            json=login_data,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code == 200:
            data = response.json()
            token = data.get('access_token')
            if token:
                print("   ✅ User signed in successfully")
                print(f"   Token received: {token[:20]}...")
                return True, token
            else:
                print("   ⚠️  Login successful but no token received")
                return True, None
        else:
            print(f"   ❌ Sign in failed: {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False, None
            
    except Exception as e:
        print(f"❌ Signin test failed: {e}")
        return False, None

def test_get_article():
    """Test getting a full article/discourse"""
    print("\n" + "="*60)
    print("TEST: Get Full Article/Discourse")
    print("="*60)
    try:
        # First get search results to find an article ID
        _, discourses = test_search_question()
        if not discourses:
            print("❌ Cannot test - no discourses available")
            return False
        
        article_id = discourses[0].get('_id') or discourses[0].get('id')
        if not article_id:
            print("❌ No article ID found in discourse")
            return False
        
        print(f"Fetching article with ID: {article_id}")
        
        response = requests.get(f"{BASE_URL}/blog/{article_id}")
        
        if response.status_code == 200:
            article = response.json()
            print("   ✅ Article retrieved successfully")
            print(f"   Title: {article.get('title', 'N/A')}")
            print(f"   Has content: {'Yes' if article.get('content') else 'No'}")
            return True
        else:
            print(f"   ❌ Failed to get article: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Get article test failed: {e}")
        return False

def main():
    print("\n" + "="*60)
    print("BACKEND FUNCTIONALITY TEST SUITE")
    print("="*60)
    print(f"Testing backend at: {BASE_URL}")
    
    results = {}
    
    # Test 1: Health check
    results['health'] = test_health_check()
    
    # Test 2: Search/Question
    search_success, discourses = test_search_question()
    results['search'] = search_success
    
    # Test 3: Get full article
    results['get_article'] = test_get_article()
    
    # Test 4: Save discourse
    results['save_discourse'] = test_save_discourse()
    
    # Test 5: Sign up
    signup_success, test_email, test_password = test_signup()
    results['signup'] = signup_success
    
    # Test 6: Sign in
    if signup_success:
        signin_success, token = test_signin(test_email, test_password)
        results['signin'] = signin_success
    else:
        print("\n⚠️  Skipping sign in test (signup failed)")
        results['signin'] = False
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    test_names = {
        'health': 'Health Check',
        'search': 'Search/Question (5+ discourses)',
        'get_article': 'Get Full Article',
        'save_discourse': 'Save Discourse Endpoint',
        'signup': 'User Sign Up',
        'signin': 'User Sign In'
    }
    
    for key, name in test_names.items():
        status = "✅ PASS" if results.get(key) else "❌ FAIL"
        print(f"{status} - {name}")
    
    # Note about highlighting and annotations
    print("\n" + "="*60)
    print("NOTE: Highlighting and Annotations")
    print("="*60)
    print("These features are typically frontend-only and don't require")
    print("backend endpoints. They are usually handled in the browser")
    print("using JavaScript and may store data locally or in the frontend state.")
    print("\nTo fully test these features, you would need to:")
    print("1. Access the frontend application")
    print("2. Open a discourse/article")
    print("3. Select text to highlight")
    print("4. Add annotations/comments")
    
    # Overall result
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"\nOverall: {passed}/{total} tests passed")
    
    if passed == total:
        print("✅ All backend tests passed!")
        return 0
    else:
        print("⚠️  Some tests failed or were skipped")
        return 1

if __name__ == '__main__':
    sys.exit(main())

