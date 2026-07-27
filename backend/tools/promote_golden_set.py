"""Promote golden_questions_draft.json to golden_questions.json.

Three things happen here:

1. Applies the label corrections agreed during review (see LABEL_FIXES).
2. Adds EXACT-DISCOURSE cases. These are a new expectation type: when a user
   names a specific discourse, that discourse must come back FIRST — ideally as
   the only result. Measured before adding them, naming a discourse verbatim
   without quotes did NOT retrieve it ("Dharma is Immutable" returned 10 results
   led by "True Nature Of Rama"), while the quoted form returned exactly one
   correct hit. So this category is a real gap, not a formality.
3. Strips review-only fields (_bucket) and renumbers ids.

Titles are read live from Weaviate so a case can never reference a discourse the
corpus doesn't actually have.

    venv/bin/python promote_golden_set.py            # writes golden_questions.json
    venv/bin/python promote_golden_set.py --dry-run  # print, write nothing
"""

import os as _os, sys as _sys
# This script lives in a subdirectory but imports the backend's top-level
# modules (search, weaviate_client, …), so put the backend root on sys.path
# before those imports. Keeps the script runnable from anywhere.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))


import argparse
import collections
import json
import sys

from dotenv import load_dotenv

load_dotenv()

from weaviate_client import get_client  # noqa: E402

DRAFT = "golden_questions_draft.json"
OUT = "golden_questions.json"

# Corrections agreed during review. Keyed by the normalized question.
LABEL_FIXES = {
    "what should i do with my life":
        {"expect": "answer", "intent_hint": "scenario"},
    "difference between conscience vs conscious":
        {"expect": "answer", "intent_hint": "comparative"},
    "how does sathya sai baba define dharma":
        {"expect": "answer", "intent_hint": "conceptual"},
    "what does swami say about kumbh mela?":
        {"expect": "answer", "intent_hint": "occasion"},
    "when was jesus born":
        {"expect": "abstain", "intent_hint": "out_of_domain"},
}

# Collections whose chapters are numbered and enumerable — "Chapter N of X" must
# resolve to exactly one discourse.
CHAPTERED = ["Prema Vahini", "Dharma Vahini", "Prasanthi Vahini"]


def norm(s):
    return " ".join((s or "").lower().split())


def load_titles():
    """Real (title, collection, chapter_index) triples, straight from the corpus."""
    client = get_client()
    col = client.collections.get("Article")
    by_coll = collections.defaultdict(list)
    for o in col.query.fetch_objects(
        limit=4000, return_properties=["title", "collection_name", "chapter_index"]
    ).objects:
        p = o.properties
        t = (p.get("title") or "").strip()
        cn = (p.get("collection_name") or "").strip()
        if t and cn:
            by_coll[cn].append((t, p.get("chapter_index")))
    client.close()
    return by_coll


def build_exact_cases(by_coll):
    """One case per phrasing style, over real titles from several collections.

    Styles are deliberately varied because they exercise different routes: a bare
    title goes to semantic search, a quoted title to the exact-phrase shortcut,
    and "Chapter N of X" to the listing route. All three must land on the same
    discourse.
    """
    cases = []

    # Distinctive, unambiguous titles from the chaptered Vahini texts.
    for cn in CHAPTERED:
        entries = [(t, ci) for t, ci in by_coll.get(cn, []) if ci is not None]
        entries.sort(key=lambda x: x[1])
        for title, ci in entries[:2]:
            cases.append({"question": title,
                          "expect": "exact", "expect_title": title,
                          "intent_hint": "named_text"})
            cases.append({"question": f'"{title}"',
                          "expect": "exact", "expect_title": title,
                          "intent_hint": "named_text"})
            cases.append({"question": f"the discourse titled {title}",
                          "expect": "exact", "expect_title": title,
                          "intent_hint": "named_text"})
            cases.append({"question": f"{cn} Chapter {ci + 1}",
                          "expect": "exact", "expect_title": title,
                          "intent_hint": "listing"})
            cases.append({"question": f"show me Chapter {ci + 1} of {cn}",
                          "expect": "exact", "expect_title": title,
                          "intent_hint": "listing"})

    # A few long-title discourses from the largest collection — these are the
    # ones a user is most likely to name verbatim after seeing a citation.
    sss = [t for t, _ in by_coll.get("Sathya Sai Speaks", [])]
    distinctive = [t for t in sss if 25 <= len(t) <= 60][:5]
    for title in distinctive:
        cases.append({"question": title, "expect": "exact",
                      "expect_title": title, "intent_hint": "named_text"})
        cases.append({"question": f'"{title}"', "expect": "exact",
                      "expect_title": title, "intent_hint": "named_text"})
    return cases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        draft = json.load(open(DRAFT))
    except FileNotFoundError:
        sys.exit(f"{DRAFT} not found — run build_golden_set.py first.")

    fixed = 0
    out = []
    for g in draft:
        g = {k: v for k, v in g.items() if not k.startswith("_")}
        fix = LABEL_FIXES.get(norm(g["question"]))
        if fix:
            g.update(fix)
            fixed += 1
        out.append(g)

    print(f"Loaded {len(out)} from draft; applied {fixed}/{len(LABEL_FIXES)} label fixes")
    if fixed != len(LABEL_FIXES):
        missing = [q for q in LABEL_FIXES if not any(norm(g["question"]) == q for g in out)]
        print(f"  !! not found in draft: {missing}")

    by_coll = load_titles()
    exact = build_exact_cases(by_coll)
    seen = {norm(g["question"]) for g in out}
    exact = [c for c in exact if norm(c["question"]) not in seen]
    out.extend(exact)
    print(f"Added {len(exact)} exact-discourse cases")

    for i, g in enumerate(out, 1):
        g["id"] = f"q{i:03d}"

    dist = collections.Counter(g["expect"] for g in out)
    print(f"\nTotal {len(out)} questions; expect distribution: {dict(dist)}")

    if args.dry_run:
        print("\nSample exact cases:")
        for c in exact[:8]:
            print(f"  {c['question'][:62]!r:66s} -> {c['expect_title'][:40]!r}")
        print("\nDRY RUN — nothing written.")
        return

    json.dump(out, open(OUT, "w"), indent=2)
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
