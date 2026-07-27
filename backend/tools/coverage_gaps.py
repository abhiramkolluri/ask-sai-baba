"""Content-coverage gap diagnostic for the discourse corpus.

Read-only analysis over a completed replay (real_replay_results.json from
replay_real_questions.py). It answers: which topics do real users ask about
repeatedly that the corpus barely covers? Those are ingestion priorities for the
content team — not search bugs.

Method: cluster ALL replayed questions by topic (greedy keyword-overlap), then for
each cluster compute demand (how many phrasings/threads asked) vs coverage (the
BEST result count any phrasing achieved). A topic asked seven ways whose best
attempt still returns one discourse is a real gap; clustering all questions (not
just the weak ones) means a single lucky phrasing can't hide the ceiling.

    venv/bin/python coverage_gaps.py

Output: coverage_gaps.json + a printed ranked "most-wanted, thinnest-coverage" list.
"""

import json
import re

IN_FILE = "real_replay_results.json"
OUT_FILE = "coverage_gaps.json"

# Words too generic to define a topic; dropped from a question's signature.
STOPWORDS = {
    "the", "and", "for", "are", "was", "what", "how", "why", "who", "when", "where",
    "does", "did", "can", "you", "your", "our", "his", "her", "their", "some", "give",
    "find", "help", "tell", "about", "with", "from", "that", "this", "these", "those",
    "want", "need", "more", "into", "learn", "discourse", "discourses", "swami", "baba",
    "sathya", "sai", "say", "says", "said", "talks", "talk", "based", "there", "them",
    "have", "has", "get", "should", "would", "could", "will", "please", "know", "make",
    "life", "spiritual", "teachings", "teaching", "quote", "quotes", "sources", "source",
}

# A cluster absorbs a question when their signatures overlap at least this much.
JACCARD_THRESHOLD = 0.4
# Coverage at or below this best-case result count marks a cluster as a thin gap.
THIN_COVERAGE = 2


def signature(question):
    """Significant content words of a question (lowercased, de-stopworded)."""
    words = re.findall(r"[a-z]+", question.lower())
    return {w for w in words if len(w) > 3 and w not in STOPWORDS}


def jaccard(a, b):
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def main():
    data = json.load(open(IN_FILE))
    entries = [v for v in data.values() if "trace" in v]

    # Greedy single-pass clustering by signature overlap.
    clusters = []  # each: {"sig": set, "members": [entry], "peak": str}
    for e in entries:
        sig = signature(e["question"])
        if not sig:
            continue
        best, best_j = None, 0.0
        for c in clusters:
            j = jaccard(sig, c["sig"])
            if j > best_j:
                best, best_j = c, j
        if best and best_j >= JACCARD_THRESHOLD:
            best["members"].append(e)
            best["sig"] |= sig  # let the cluster's vocabulary grow
        else:
            clusters.append({"sig": set(sig), "members": [e]})

    # Score each cluster: demand vs best-case coverage.
    rows = []
    for c in clusters:
        members = c["members"]
        demand = len(members)
        result_counts = [len(m["results"]) for m in members]
        coverage = max(result_counts)  # best any phrasing achieved
        peak = max(members, key=lambda m: len(m["results"]))
        rows.append({
            "demand": demand,
            "best_coverage": coverage,
            "worst_coverage": min(result_counts),
            "example_questions": [m["question"][:80] for m in members[:5]],
            "best_phrasing": peak["question"][:80],
            "keywords": sorted(c["sig"])[:8],
        })

    # Gaps: asked more than once AND best case is thin. Rank by demand, then by
    # how thin the coverage is.
    gaps = [r for r in rows if r["demand"] >= 2 and r["best_coverage"] <= THIN_COVERAGE]
    gaps.sort(key=lambda r: (-r["demand"], r["best_coverage"]))

    with open(OUT_FILE, "w") as f:
        json.dump({"gaps": gaps, "all_clusters": len(clusters)}, f, indent=2)

    print(f"{len(entries)} questions -> {len(clusters)} topic clusters; "
          f"{len(gaps)} high-demand/thin-coverage gaps\n")
    print("Most-wanted, thinnest-coverage topics (ingestion priorities):")
    for r in gaps:
        print(f"  demand={r['demand']} best_results={r['best_coverage']}  "
              f"keywords={r['keywords']}")
        print(f"      e.g. {r['example_questions'][0]!r}")
    print(f"\nFull detail -> {OUT_FILE}")


if __name__ == "__main__":
    main()
