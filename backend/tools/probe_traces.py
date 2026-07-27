"""Adversarial probe harness for the search pipeline, driven by the /search trace.

Like the other test_*.py files here, this is an integration script that hits a
RUNNING server (default http://localhost:8000), not a pytest suite:

    venv/bin/python probe_traces.py

Method: each probe sends one query with include_trace=true and records the full
trace. Nuanced queries are PAIRED with their stripped bare-topic version — if the
pair produces near-identical facets/results (high Jaccard overlap of citation
ids), the pipeline dropped the nuance (the "value of Truth" -> "truth" hole).
Every trace is saved to probe_results.json for analysis; the console output is a
compact summary per probe plus pair-overlap lines.

The probe list maps one-to-one onto suspected failure modes by pipeline stage:
  A* planner (plan_queries) nuance drops    C* retrieval/merge mechanics
  B* grader facet-vs-question mismatch      D* exact-phrase path
  (B is analyzed from A's traces)           E* multi-turn resolution
"""

import json
import time
import urllib.request

BASE_URL = "http://localhost:8000"
OUT_FILE = "probe_results.json"

# (id, category, query, history, pair_with) — pair_with names the bare-topic
# probe id this one should be diffed against (None for standalone probes).
PROBES = [
    # --- A1: aspect qualifiers (the known "value of Truth" class) ---
    ("truth-bare",        "A1", "truth", None, None),
    ("truth-value",       "A1", "Help me find some discourses to learn more about the value of Truth", None, "truth-bare"),
    ("devotion-bare",     "A1", "devotion", None, None),
    ("devotion-obstacles","A1", "what are the obstacles to devotion", None, "devotion-bare"),
    ("meditation-bare",   "A1", "meditation", None, None),
    ("meditation-stages", "A1", "what are the stages of meditation", None, "meditation-bare"),
    ("progress-signs",    "A1", "what are the signs of spiritual progress", None, None),

    # --- A2: question type (why vs how vs bare) ---
    ("silence-bare", "A2", "silence", None, None),
    ("silence-why",  "A2", "why does Swami emphasize silence", None, "silence-bare"),
    ("silence-how",  "A2", "how do I practice silence in daily life", None, "silence-bare"),

    # --- A3: negation / deontic frame ---
    ("anger-not",  "A3", "what should we NOT do when we are angry", None, None),
    ("lie-okay",   "A3", "is it ever acceptable to tell a lie", None, None),

    # --- A4: comparison (no stage performs comparisons) ---
    ("bhakti-vs-jnana", "A4", "what is the difference between bhakti and jnana", None, None),

    # --- A5: audience qualifier ---
    ("devotion-students", "A5", "how should students practice devotion", None, "devotion-bare"),

    # --- A6: story requests (distillation may destroy the story reference) ---
    ("sandalwood-story", "A6", "the story Swami tells about the sandalwood tree", None, None),

    # --- A7: enumerations ---
    ("five-values", "A7", "what are the five human values", None, None),
    ("three-ps",    "A7", "what are the three P's Swami speaks of", None, None),

    # --- A8: metadata questions (pipeline has no metadata filters) ---
    ("dasara",   "A8", "discourses given during Dasara", None, None),
    ("birthday", "A8", "what did Swami say in his birthday discourse", None, None),

    # --- A9: intent hallucination (off-domain / gibberish controls) ---
    ("gibberish", "A9", "asdkjfhq zzkw qwptv", None, None),
    ("pizza",     "A9", "what is the best pizza in Bangalore", None, None),

    # --- C1: facet starvation on multi-topic questions ---
    ("multi-topic", "C1", "I am angry at my brother, I cannot forgive him, and I worry about money", None, None),

    # --- C2: transliteration robustness ---
    ("prema",   "C2", "prema", None, None),
    ("santhi",  "C2", "how to attain shanti", None, None),

    # --- D1: exact-phrase path (extract_quoted_phrase lowercases the phrase) ---
    ("phrase-title", "D1", 'discourse on "Love All Serve All"', None, None),
    ("phrase-upper", "D1", 'discourse on "LOVE ALL SERVE ALL"', None, None),
    ("phrase-punct", "D1", 'find "love all, serve all"', None, None),

    # --- E1: multi-turn reference + new qualifier ---
    ("children-meditate", "E1", "what about for children?", ["how should I meditate"], None),
]


def run_probe(query, history):
    body = {"query": query, "include_trace": True}
    if history:
        body["history"] = history
    req = urllib.request.Request(
        BASE_URL + "/search",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.load(resp)


def jaccard(ids_a, ids_b):
    a, b = set(ids_a), set(ids_b)
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def summarize(pid, data):
    t = data["trace"]
    results = data["results"]
    print(f"\n[{pid}] quality={t['quality']} results={len(results)}")
    print(f"  facets: {t['planning']['facets']}")
    g = t["grading"]
    print(f"  grading: kept={g['kept']} rejected={g['rejected']} fallback={g['fallback']}")
    if t["exact_phrase"]["phrase"]:
        print(f"  exact_phrase: {t['exact_phrase']}")
    print(f"  result titles: {[r['title'][:60] for r in results[:6]]}")
    if t["reasons"]:
        print(f"  reasons: {[r['code'] for r in t['reasons']]}")


def main():
    collected = {}
    for pid, cat, query, history, pair in PROBES:
        try:
            data = run_probe(query, history)
        except Exception as e:
            print(f"\n[{pid}] FAILED: {e}")
            collected[pid] = {"error": str(e), "category": cat, "query": query}
            continue
        collected[pid] = {
            "category": cat,
            "query": query,
            "history": history,
            "pair_with": pair,
            "results": data["results"],
            "trace": data["trace"],
        }
        summarize(pid, data)
        time.sleep(1)  # be polite: each probe costs 2 LLM calls + a Cohere rerank

    # Pair diffs: high overlap where the nuance demanded different results = hole.
    print("\n" + "=" * 60)
    print("PAIR OVERLAPS (result-id Jaccard; high = nuance dropped)")
    print("=" * 60)
    for pid, cat, query, history, pair in PROBES:
        if not pair or pid not in collected or pair not in collected:
            continue
        a = collected[pid].get("results")
        b = collected[pair].get("results")
        if a is None or b is None:
            continue
        j = jaccard([r["_id"] for r in a], [r["_id"] for r in b])
        fa = collected[pid]["trace"]["planning"]["facets"]
        fb = collected[pair]["trace"]["planning"]["facets"]
        print(f"  {pid} vs {pair}: jaccard={j:.2f}")
        print(f"    nuanced facets: {fa}")
        print(f"    bare facets:    {fb}")

    with open(OUT_FILE, "w") as f:
        json.dump(collected, f, indent=2)
    print(f"\nSaved {len(collected)} traces to {OUT_FILE}")


if __name__ == "__main__":
    main()
