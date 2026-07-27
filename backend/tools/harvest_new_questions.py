"""Harvest a FRESH batch of real user questions not previously tested.

Read-only against Weaviate. Excludes every question already in real_questions.json
(the prior harvest) plus the 4 default sample questions, and draws mainly from the
large untapped UserQuery pool (~3,400 rows, only 20 used last time). Filters out
junk (empty, single-token, code/injection probes) so the adversarial audit
targets genuine questions.

    venv/bin/python harvest_new_questions.py

Output: real_questions_new.json — [{id, source, question, history}]
User emails are not written out; only question text.
"""

import os as _os, sys as _sys
# This script lives in a subdirectory but imports the backend's top-level
# modules (search, weaviate_client, …), so put the backend root on sys.path
# before those imports. Keeps the script runnable from anywhere.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_ROOT = _os.path.dirname(_HERE)


def _here(name):
    """A committed fixture that lives beside this script."""
    return _os.path.join(_HERE, name)


def _artifact(name):
    """A generated run output. Kept out of the source tree in artifacts/."""
    d = _os.path.join(_ROOT, "artifacts")
    _os.makedirs(d, exist_ok=True)
    return _os.path.join(d, name)


import json
import re

from dotenv import load_dotenv

load_dotenv()

from weaviate_client import get_client  # noqa: E402
from weaviate.classes.query import Sort  # noqa: E402

PRIOR_FILE = _artifact("real_questions.json")
OUT_FILE = _artifact("real_questions_new.json")
TARGET = 80

DEFAULT_QUESTIONS = {
    "Help me find some discourses to learn more about the value of Truth",
    "What does Swami say about stopping bad habits and learning good ones?",
    "My goal is to be more mindful in my daily life. Find discourses that can inspire me.",
    "I want to learn more about the importance of compassion through reading discourses.",
}


def _norm(q):
    return " ".join(q.lower().split())


def _is_junk(q):
    """Drop empty, single-word, or code/injection-probe queries."""
    if len(q.split()) < 2:
        return True
    if re.search(r"__schema|query\s*\{|\bmutation\b|<[a-z]+>|SELECT\s|;\s*--", q, re.I):
        return True
    if not re.search(r"[a-zA-Z]{3,}", q):
        return True
    return False


def harvest():
    client = get_client()

    # Everything already tested (prior harvest) + defaults => the exclude set.
    seen = {_norm(q) for q in DEFAULT_QUESTIONS}
    try:
        for e in json.load(open(PRIOR_FILE)):
            seen.add(_norm(e["question"]))
    except FileNotFoundError:
        pass
    prior_count = len(seen)

    entries = []

    # Primary source: the large untapped UserQuery pool, most-recent first.
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
        entries.append({"id": f"uq-new-{len(entries):03d}", "source": "UserQuery",
                        "question": q, "history": []})

    with open(OUT_FILE, "w") as f:
        json.dump(entries, f, indent=2)

    print(f"Excluded {prior_count} already-seen/default questions.")
    print(f"Harvested {len(entries)} NEW questions -> {OUT_FILE}")
    for e in entries[:12]:
        print(f"  - {e['question'][:90]!r}")


if __name__ == "__main__":
    harvest()
