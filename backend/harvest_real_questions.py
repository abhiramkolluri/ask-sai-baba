"""Harvest real user questions from Weaviate for adversarial replay.

Read-only against Weaviate. Like the other test_*/probe_* scripts here, run it
directly with the venv interpreter:

    venv/bin/python harvest_real_questions.py

Sources:
  - ChatThread.messages_json — every question users asked through the UI, in
    thread order, so each question can be replayed WITH its real in-thread
    history (up to 3 prior questions — mirroring ChatBox.handleSend).
  - UserQuery.query_text — the most recent distinct queries logged by the legacy
    /query path (no history available for these).

The four default sample questions (SampleQuestions.jsx) are excluded — the point
is to study questions users composed themselves. User emails are deliberately
NOT written to the output; only question text and history.

Output: real_questions.json — [{id, source, question, history}]
"""

import json

from dotenv import load_dotenv

load_dotenv()

from weaviate_client import get_client  # noqa: E402  (needs env loaded first)

OUT_FILE = "real_questions.json"
USERQUERY_SAMPLE = 20

# The 4 frontend sample questions (SampleQuestions.jsx) — clicked, not composed.
DEFAULT_QUESTIONS = {
    "Help me find some discourses to learn more about the value of Truth",
    "What does Swami say about stopping bad habits and learning good ones?",
    "My goal is to be more mindful in my daily life. Find discourses that can inspire me.",
    "I want to learn more about the importance of compassion through reading discourses.",
}


def _norm(q):
    """Dedupe key: case/whitespace-insensitive."""
    return " ".join(q.lower().split())


def harvest():
    client = get_client()
    entries = []
    seen = {_norm(q) for q in DEFAULT_QUESTIONS}

    # --- ChatThread: questions with their real in-thread history ---
    threads = client.collections.get("ChatThread")
    for obj in threads.iterator(return_properties=["messages_json"]):
        try:
            msgs = json.loads(obj.properties.get("messages_json") or "[]")
        except Exception:
            continue
        thread_questions = []
        for m in msgs:
            q = (m.get("question") or "").strip()
            if not q:
                continue
            # History mirrors the frontend: the questions asked before this one
            # in the same thread, most recent 3.
            history = thread_questions[-3:]
            thread_questions.append(q)
            if _norm(q) in seen:
                continue
            seen.add(_norm(q))
            entries.append({
                "id": f"thread-{len(entries):03d}",
                "source": "ChatThread",
                "question": q,
                "history": history,
            })

    # --- UserQuery: most recent distinct queries as a supplement ---
    uq = client.collections.get("UserQuery")
    from weaviate.classes.query import Sort
    resp = uq.query.fetch_objects(
        limit=500,
        sort=Sort.by_property("created_at", ascending=False),
        return_properties=["query_text"],
    )
    added = 0
    for obj in resp.objects:
        if added >= USERQUERY_SAMPLE:
            break
        q = (obj.properties.get("query_text") or "").strip()
        if not q or _norm(q) in seen:
            continue
        seen.add(_norm(q))
        entries.append({
            "id": f"uq-{added:03d}",
            "source": "UserQuery",
            "question": q,
            "history": [],
        })
        added += 1

    with open(OUT_FILE, "w") as f:
        json.dump(entries, f, indent=2)

    n_thread = sum(1 for e in entries if e["source"] == "ChatThread")
    n_uq = len(entries) - n_thread
    n_hist = sum(1 for e in entries if e["history"])
    print(f"Harvested {len(entries)} questions -> {OUT_FILE}")
    print(f"  ChatThread: {n_thread} ({n_hist} with history) | UserQuery: {n_uq}")


if __name__ == "__main__":
    harvest()
