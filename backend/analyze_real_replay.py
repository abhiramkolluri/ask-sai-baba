"""Summarize the real-question replay (real_replay_results.json) for review.

Prints the fixed-class scorecard inputs and groups flagged traces by flag so the
manual analysis can start from the suspicious cases. Read-only over the output
file; run after replay_real_questions.py completes:

    venv/bin/python analyze_real_replay.py
"""

import json
import statistics
from collections import Counter

d = json.load(open("real_replay_results.json"))
ok = {k: v for k, v in d.items() if "trace" in v}
errors = {k: v for k, v in d.items() if "error" in v}

print(f"replayed={len(ok)} errors={len(errors)}")
if errors:
    for k, v in errors.items():
        print(f"  ERROR {k}: {v['question'][:60]!r} -> {v['error'][:80]}")

# --- Overall distributions ---
quality = Counter(v["trace"]["quality"] for v in ok.values())
print(f"\nquality: {dict(quality)}")

reasons = Counter(r["code"] for v in ok.values() for r in v["trace"]["reasons"])
print(f"reason codes: {dict(reasons)}")

totals = [v["trace"]["timings_ms"].get("total", 0) for v in ok.values()]
print(f"latency ms: median={statistics.median(totals):.0f} p90={sorted(totals)[int(len(totals)*0.9)]} max={max(totals)}")

nfacets = Counter(len(v["trace"]["planning"]["facets"]) for v in ok.values())
print(f"facet counts: {dict(sorted(nfacets.items()))}")

flag_counts = Counter(f for v in ok.values() for f in v["flags"])
print(f"flags: {dict(flag_counts.most_common())}")

# --- Flag groups for manual review ---
def show(v, extra=""):
    t = v["trace"]
    print(f"  [{v['id']}] {v['question'][:80]!r}")
    print(f"      facets={t['planning']['facets']} q={t['quality']} n={len(v['results'])}{extra}")

for flag in ["ssio_leak", "grader_fallback", "planner_passthrough", "slow_gt25s",
             "exact_phrase_path", "occasion_routing", "aspect_drop_candidate",
             "zero_results", "quality_partial"]:
    group = [v for v in ok.values() if flag in v["flags"]]
    if not group:
        continue
    print(f"\n=== {flag} ({len(group)}) ===")
    for v in group[:12]:
        if flag == "exact_phrase_path":
            show(v, extra=f" exact={v['trace']['exact_phrase']}")
        elif flag == "occasion_routing":
            show(v, extra=f" mf={v['trace'].get('metadata_filter')}")
        elif flag == "slow_gt25s":
            show(v, extra=f" total={v['trace']['timings_ms'].get('total')}ms")
        else:
            show(v)
