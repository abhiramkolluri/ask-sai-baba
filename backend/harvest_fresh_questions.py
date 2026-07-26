"""Harvest a THIRD, genuinely-unseen batch of real user questions.

Read-only against Weaviate. Excludes both prior harvests (real_questions.json and
real_questions_new.json) plus the 4 default sample questions, drawing again from
the large untapped UserQuery pool. Same junk filter and privacy stance as
harvest_new_questions.py (user emails never written; output gitignored).

    venv/bin/python harvest_fresh_questions.py

Output: real_questions_v3.json — [{id, source, question, history}]
"""

import json
import re

from dotenv import load_dotenv

load_dotenv()

from weaviate_client import get_client  # noqa: E402
from weaviate.classes.query import Sort  # noqa: E402

PRIOR_FILES = ["real_questions.json", "real_questions_new.json"]
OUT_FILE = "real_questions_v3.json"
TARGET = 70

DEFAULT_QUESTIONS = {
    "Help me find some discourses to learn more about the value of Truth",
    "What does Swami say about stopping bad habits and learning good ones?",
    "My goal is to be more mindful in my daily life. Find discourses that can inspire me.",
    "I want to learn more about the importance of compassion through reading discourses.",
}


def _norm(q):
    return " ".join(q.lower().split())


def _is_junk(q):
    if len(q.split()) < 2:
        return True
    if re.search(r"__schema|query\s*\{|\bmutation\b|<[a-z]+>|SELECT\s|;\s*--", q, re.I):
        return True
    if not re.search(r"[a-zA-Z]{3,}", q):
        return True
    return False


def harvest():
    client = get_client()

    seen = {_norm(q) for q in DEFAULT_QUESTIONS}
    for pf in PRIOR_FILES:
        try:
            for e in json.load(open(pf)):
                seen.add(_norm(e["question"]))
        except FileNotFoundError:
            pass
    prior_count = len(seen)

    entries = []
    uq = client.collections.get("UserQuery")
    resp = uq.query.fetch_objects(
        limit=3000,
        sort=Sort.by_property("created_at", ascending=False),
        return_properties=["query_text"],
    )
    for obj in resp.objects:
        if len(entries) >= TARGET:
            break
        q = (obj.properties.get("query_text") or "").strip()
        if not q or _norm(q) in seen or _is_junk(q):
            continue
        seen.add(_norm(q))
        entries.append({"id": f"v3-{len(entries):03d}", "source": "UserQuery",
                        "question": q, "history": []})

    with open(OUT_FILE, "w") as f:
        json.dump(entries, f, indent=2)

    print(f"Excluded {prior_count} already-seen/default questions.")
    print(f"Harvested {len(entries)} FRESH questions -> {OUT_FILE}")
    for e in entries[:12]:
        print(f"  - {e['question'][:90]!r}")


if __name__ == "__main__":
    harvest()
