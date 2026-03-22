import os
import sys
import requests

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")

def print_result(name, passed, details=""):
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"{status} | {name}")
    if details:
        print(f"   -> {details}")

def main():
    print(f"Starting test against {BASE_URL}...")
    
    total = 0
    passed = 0
    fails = []
    
    # helper for assertions
    def assert_test(name, fn):
        nonlocal total, passed, fails
        total += 1
        try:
            passed_test, details = fn()
            if passed_test:
                passed += 1
                print_result(name, True, details)
            else:
                fails.append((name, details))
                print_result(name, False, details)
        except requests.exceptions.ConnectionError:
            print(f"❌ FAIL | {name}")
            print("   -> Connection error: Is the backend running?")
            sys.exit(1)
        except Exception as e:
            fails.append((name, str(e)))
            print_result(name, False, f"Exception: {e}")

    # a) GET /
    def test_health():
        r = requests.get(f"{BASE_URL}/")
        if r.status_code != 200: return False, f"Status {r.status_code}"
        data = r.json()
        if data.get("status") != "healthy": return False, "Missing 'healthy' status"
        if "vector_store_healthy" not in data: return False, "Missing vector_store_healthy key"
        return True, f"vector_store_healthy: {data['vector_store_healthy']}"
    assert_test("Health Check (GET /)", test_health)

    # b) POST /search
    first_id = None
    def test_search():
        nonlocal first_id
        r = requests.post(f"{BASE_URL}/search", json={"query": "What does Swami say about truth?"})
        if r.status_code != 200: return False, f"Status {r.status_code}"
        data = r.json()
        if not isinstance(data, list) or len(data) == 0: return False, "Response not a populated list"
        
        item = data[0]
        keys = ["_id", "title", "content", "score", "location", "occasion", "link", "collection"]
        for k in keys:
            if k not in item and k != 'collection': 
                return False, f"Missing key: {k}"
            elif k == 'collection' and 'collection' not in item and 'collection_name' not in item:
                return False, f"Missing key: {k}"
        
        if not isinstance(item["score"], (int, float)): return False, "Score is not a number"
        
        first_id = item.get("_id") or item.get("id")
        return True, f"Count: {len(data)}, First Title: {item['title']}"
    assert_test("Search (POST /search)", test_search)

    # c) POST /query
    def test_query():
        r = requests.post(f"{BASE_URL}/query", json={"query": "How can I develop more compassion?"})
        if r.status_code != 200: return False, f"Status {r.status_code}"
        data = r.json()
        if "response" not in data: return False, "Missing 'response' key"
        if not isinstance(data["response"], str) or not data["response"]: return False, "Empty or invalid response string"
        if "session_id" not in data: return False, "Missing 'session_id'"
        return True, f"Response sample: {data['response'][:100]}..."
    assert_test("Query (POST /query)", test_query)

    # d) POST /summarize-question
    def test_summary():
        r = requests.post(f"{BASE_URL}/summarize-question", json={"query": "What is the spiritual significance of selfless service according to Sathya Sai Baba?"})
        if r.status_code != 200: return False, f"Status {r.status_code}"
        data = r.json()
        if "summary" not in data: return False, "Missing 'summary' key"
        words = data["summary"].split()
        if len(words) > 10: return False, f"Summary too long ({len(words)} words)"
        return True, f"Summary: {data['summary']}"
    assert_test("Summarize (POST /summarize-question)", test_summary)

    # e) GET /blog/<id>
    def test_blog():
        if not first_id: return False, "Skipped due to missing ID from search test"
        r = requests.get(f"{BASE_URL}/blog/{first_id}")
        if r.status_code != 200: return False, f"Status {r.status_code}"
        data = r.json()
        
        keys = ["title", "content", "location", "occasion", "link"]
        for k in keys:
            if k not in data: return False, f"Missing key: {k}"
        if "collection" not in data and "collection_name" not in data:
            return False, "Missing collection/collection_name map"
        if "markdown_format" not in data:
            return False, "Missing markdown_format"
        return True, f"Title: {data['title']}"
    assert_test("Get Article (GET /blog/<id>)", test_blog)

    # f) POST /api/feedback
    def test_feedback():
        payload = {
            "question": "test question",
            "answer": "test answer",
            "feedbackType": "up",
            "reason": "Helpful",
            "additionalComments": "Great response",
            "timestamp": "2024-01-01T00:00:00Z",
            "citations": []
        }
        r = requests.post(f"{BASE_URL}/api/feedback", json=payload)
        if r.status_code not in [200, 201]: return False, f"Status {r.status_code}"
        return True, f"Response: {r.text}"
    assert_test("Feedback (POST /api/feedback)", test_feedback)

    # g) POST /conversation/clear
    def test_clear_conv():
        r = requests.post(f"{BASE_URL}/conversation/clear", json={"session_id": "test-session", "user_id": "test-user"})
        if r.status_code != 200: return False, f"Status {r.status_code}"
        return True, "Conversation cleared"
    assert_test("Clear Conv (POST /conversation/clear)", test_clear_conv)

    # h) GET /conversation/history
    def test_conv_history():
        r = requests.get(f"{BASE_URL}/conversation/history", params={"session_id": "test-session", "user_id": "test-user"})
        if r.status_code != 200: return False, f"Status {r.status_code}"
        data = r.json()
        if "messages" not in data: return False, "Missing 'messages' key"
        if len(data["messages"]) != 0: return False, f"Messages not empty: {len(data['messages'])}"
        return True, "History is empty as expected"
    assert_test("Conv History (GET /conversation/history)", test_conv_history)

    print("\n" + "="*40)
    print(f"TEST SUMMARY: {passed}/{total} PASSED")
    print("="*40)
    for name, err in fails:
        print(f"❌ {name}: {err}")
        
    print("\nNote: Endpoints requiring Auth0/Google Auth via @require_auth (chats, saved-discourses) skipped.")
    print("Run `AUTH_TOKEN=your_token EMAIL=your_email python test_auth_endpoints.py` to test authenticated CRUD manually.")
    
    if fails:
        sys.exit(1)

if __name__ == "__main__":
    main()
