import os
import sys
import requests

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN")
EMAIL = os.environ.get("EMAIL")

def print_result(name, passed, details=""):
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"{status} | {name}")
    if details:
        print(f"   -> {details}")

def main():
    if not AUTH_TOKEN or not EMAIL:
        print("Missing AUTH_TOKEN or EMAIL environment variables.")
        print("Usage: AUTH_TOKEN=your_token EMAIL=your_email python test_auth_endpoints.py")
        sys.exit(1)
        
    print(f"Starting auth tests against {BASE_URL} for {EMAIL}...")
    headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}
    
    total = 0
    passed = 0
    fails = []
    
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

    thread_id = None
    
    # 1. POST /chats/<email>
    def test_create_thread():
        nonlocal thread_id
        r = requests.post(f"{BASE_URL}/chats/{EMAIL}", headers=headers, json={"user_email": EMAIL, "title": "Test Thread"})
        if r.status_code != 201: return False, f"Status {r.status_code} - {r.text}"
        data = r.json()
        if "id" not in data: return False, "Missing id in response"
        thread_id = data["id"]
        return True, f"Created Thread ID: {thread_id}"
    assert_test("Create Chat Thread (POST /chats/<email>)", test_create_thread)

    # 2. GET /chats/<email>
    def test_get_chats():
        r = requests.get(f"{BASE_URL}/chats/{EMAIL}", headers=headers)
        if r.status_code != 200: return False, f"Status {r.status_code} - {r.text}"
        data = r.json()
        if not isinstance(data, list): return False, "Response not a list"
        found = any(t.get("id") == thread_id for t in data)
        if not found: return False, "Newly created thread not found in list"
        return True, f"Found {len(data)} threads"
    assert_test("Get User Chats (GET /chats/<email>)", test_get_chats)

    # 3. PUT /chats/<thread_id>
    def test_put_chat():
        if not thread_id: return False, "Skipped due to missing thread_id"
        r = requests.put(f"{BASE_URL}/chats/{thread_id}", headers=headers, json={"user_email": EMAIL, "title": "Updated Test Thread"})
        if r.status_code != 200: return False, f"Status {r.status_code} - {r.text}"
        return True, "Title updated successfully"
    assert_test("Update Chat Title (PUT /chats/<thread_id>)", test_put_chat)

    # 4. POST /chats/<email>/<thread_id>/messages
    def test_post_message():
        if not thread_id: return False, "Skipped due to missing thread_id"
        r = requests.post(f"{BASE_URL}/chats/{EMAIL}/{thread_id}/messages", headers=headers, json={"question": "Testing auth", "reply": "Looks good."})
        if r.status_code != 200: return False, f"Status {r.status_code} - {r.text}"
        return True, "Message added successfully"
    assert_test("Add Message (POST /chats/<email>/<thread_id>/messages)", test_post_message)

    # 5. DELETE /chats/<thread_id>
    def test_delete_chat():
        if not thread_id: return False, "Skipped due to missing thread_id"
        r = requests.delete(f"{BASE_URL}/chats/{thread_id}", headers=headers, json={"user_email": EMAIL})
        if r.status_code != 200: return False, f"Status {r.status_code} - {r.text}"
        return True, "Thread deleted successfully"
    assert_test("Delete Chat Thread (DELETE /chats/<thread_id>)", test_delete_chat)

    discourse_id = None
    
    # 6. POST /saved-discourses/<email>
    def test_create_discourse():
        nonlocal discourse_id
        payload = {
            "article_uuid": "dummy-uuid",
            "title": "Saved Thread Testing",
            "content_preview": "This is a preview...",
            "link": "/blog/dummy-uuid",
            "collection_name": "Article"
        }
        r = requests.post(f"{BASE_URL}/saved-discourses/{EMAIL}", headers=headers, json=payload)
        if r.status_code != 201: return False, f"Status {r.status_code} - {r.text}"
        data = r.json()
        if "id" not in data: return False, "Missing id in response"
        discourse_id = data["id"]
        return True, f"Created Saved Discourse ID: {discourse_id}"
    assert_test("Create Saved Discourse (POST /saved-discourses/<email>)", test_create_discourse)

    # 7. GET /saved-discourses/<email>
    def test_get_discourses():
        r = requests.get(f"{BASE_URL}/saved-discourses/{EMAIL}", headers=headers)
        if r.status_code != 200: return False, f"Status {r.status_code} - {r.text}"
        data = r.json()
        if not isinstance(data, list): return False, "Response not a list"
        found = any(d.get("id") == discourse_id for d in data)
        if not found: return False, "Newly created discourse not found in list"
        return True, f"Found {len(data)} saved discourses"
    assert_test("Get Saved Discourses (GET /saved-discourses/<email>)", test_get_discourses)

    # 8. DELETE /saved-discourses/<email>/<id>
    def test_delete_discourse():
        if not discourse_id: return False, "Skipped due to missing discourse_id"
        r = requests.delete(f"{BASE_URL}/saved-discourses/{EMAIL}/{discourse_id}", headers=headers)
        if r.status_code != 200: return False, f"Status {r.status_code} - {r.text}"
        return True, "Saved discourse deleted properly"
    assert_test("Delete Saved Discourse (DELETE /saved-discourses/<email>/<id>)", test_delete_discourse)

    print("\n" + "="*40)
    print(f"TEST SUMMARY: {passed}/{total} PASSED")
    print("="*40)
    for name, err in fails:
        print(f"❌ {name}: {err}")
        
    if fails:
        sys.exit(1)

if __name__ == "__main__":
    main()
