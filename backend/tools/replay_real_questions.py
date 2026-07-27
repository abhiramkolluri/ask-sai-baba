
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
import os as _os, sys as _sys
"""Replay harvested real user questions through /search and flag holes.

Companion to harvest_real_questions.py (which writes real_questions.json) and
sibling of probe_traces.py — an integration script against a RUNNING server:

    venv/bin/python replay_real_questions.py

Each (question, history) is POSTed to /search with include_trace, exactly as the
frontend would send it. Every full trace is saved for analysis; a summary pass
auto-flags traces that look like holes so the manual review starts from the
suspicious ones rather than all ~110.

Output: real_replay_results.json — {id: {question, history, source, results, trace, flags}}
"""

import json
import re
import time
import urllib.request

BASE_URL = "http://localhost:8000"
IN_FILE = _artifact("real_questions.json")
OUT_FILE = _artifact("real_replay_results.json")

# Cap the run: the first N harvested questions (~15s each). Covers effectively
# the whole harvested set while leaving a small margin.
MAX_QUESTIONS = 108

# Aspect markers for the nuance-drop heuristic: if the question uses one of
# these and NO planned facet does, the planner may have dropped the asked-about
# aspect (candidate only — manual review confirms).
ASPECT_WORDS = [
    "value", "why", "how", "stages", "obstacles", "signs", "story",
    "difference", "should not", "never", "who", "when", "importance",
]


def run_one(entry):
    body = {"query": entry["question"], "include_trace": True}
    if entry["history"]:
        body["history"] = entry["history"]
    req = urllib.request.Request(
        BASE_URL + "/search",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.load(resp)


def compute_flags(entry, results, trace):
    """Heuristic hole detectors — each flag marks a trace for manual review."""
    flags = []
    q_lower = entry["question"].lower()
    facets_lower = " ".join(trace["planning"]["facets"]).lower()

    if trace["quality"] in ("none", "partial"):
        flags.append(f"quality_{trace['quality']}")
    if not results:
        flags.append("zero_results")

    hit_aspects = [w for w in ASPECT_WORDS if w in q_lower]
    if hit_aspects and not any(w in facets_lower for w in hit_aspects):
        flags.append("aspect_drop_candidate")

    # Planner passthrough of a long question = planner gave up on it.
    if trace["planning"]["facets"] == [entry["question"]] and len(entry["question"].split()) > 6:
        flags.append("planner_passthrough")

    for r in results:
        if "SSIO" in (r.get("title") or "") or (r.get("collection") == "SSIO Guidelines"):
            flags.append("ssio_leak")  # regression: fix 6 should make this impossible
            break

    if trace["exact_phrase"]["phrase"]:
        flags.append("exact_phrase_path")
    if trace.get("metadata_filter"):
        flags.append("occasion_routing")
    if trace["timings_ms"].get("total", 0) > 25000:
        flags.append("slow_gt25s")
    if trace["grading"].get("fallback"):
        flags.append("grader_fallback")
    return flags


def main():
    entries = json.load(open(IN_FILE))[:MAX_QUESTIONS]
    collected = {}
    t0 = time.time()
    for i, entry in enumerate(entries):
        try:
            data = run_one(entry)
            results, trace = data["results"], data["trace"]
            flags = compute_flags(entry, results, trace)
            collected[entry["id"]] = {**entry, "results": results, "trace": trace, "flags": flags}
            flag_note = f"  FLAGS: {flags}" if flags else ""
            print(f"[{i+1}/{len(entries)}] {entry['id']} q={trace['quality']} n={len(results)} "
                  f"{trace['timings_ms'].get('total', 0)}ms{flag_note}", flush=True)
        except Exception as e:
            collected[entry["id"]] = {**entry, "error": str(e)}
            print(f"[{i+1}/{len(entries)}] {entry['id']} ERROR: {e}", flush=True)
        # Save incrementally so an interrupted run keeps everything replayed so far.
        with open(OUT_FILE, "w") as f:
            json.dump(collected, f, indent=2)
        time.sleep(0.5)

    with open(OUT_FILE, "w") as f:
        json.dump(collected, f, indent=2)

    ok = [v for v in collected.values() if "trace" in v]
    flagged = [v for v in ok if v["flags"]]
    print(f"\nDone in {(time.time()-t0)/60:.1f} min: {len(ok)}/{len(entries)} replayed, "
          f"{len(flagged)} flagged -> {OUT_FILE}")


if __name__ == "__main__":
    main()
