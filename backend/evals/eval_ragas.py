"""Golden-set evaluation harness for the search pipeline (Phase 4).

Integration script against a running server (like probe_traces.py / replay_*).
Runs every question in golden_questions.json through /search and scores the
pipeline on retrieval-only metrics (there's no answer synthesis to grade):

  - quote_answers_rate: does the top discourse's answering quote actually answer
    the question? (LLM-judged, one gpt-4o-mini call per answered question)
  - abstention_correctness: questions labeled expect="abstain" (factual gap /
    out-of-domain / meta / named-text gap) must return no results, and questions
    labeled "answer"/"structured" must return results — i.e. no false empties and
    no spurious matches on unanswerable questions.
  - route_match: did the router send expect="structured" questions to the
    structured route, and non-abstain questions to a real answer?
  - latency p50/p90 (must stay under the ~29s API Gateway budget).

Usage:
    venv/bin/python eval_ragas.py                 # run + print scorecard
    venv/bin/python eval_ragas.py --baseline base.json   # diff vs a saved run

Writes eval_results.json (per-question detail) for baseline diffs. Use it to gate
every pipeline change: no change ships if it lowers quote_answers_rate or
abstention_correctness on the golden set.
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
import sys
import time
import statistics
import urllib.request

from dotenv import load_dotenv

load_dotenv()

from search.config import openai_client, GRADE_MODEL  # noqa: E402

import re as _re


def norm_title(t):
    """Compare titles ignoring case, punctuation and curly-quote variants — the
    corpus mixes ' and \u2019 in titles, and a user retyping one shouldn't count
    as a miss."""
    return _re.sub(r"[^a-z0-9 ]", "", (t or "").lower()).strip()


BASE_URL = "http://localhost:8000"
GOLDEN_FILE = _here("golden_questions.json")
OUT_FILE = _artifact("eval_results.json")


def search(question, history):
    body = {"query": question, "include_trace": True}
    if history:
        body["history"] = history
    req = urllib.request.Request(
        BASE_URL + "/search",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.load(resp)


def judge_quote_answers(question, quote):
    """One cheap LLM call: does this quote actually answer the question? y/n."""
    if not quote:
        return False
    try:
        r = openai_client.chat.completions.create(
            model=GRADE_MODEL,
            messages=[
                {"role": "system", "content":
                 "You judge whether a quote from a spiritual discourse directly answers a "
                 "user's question (instructs, adjudicates, or states the thing asked) rather "
                 "than merely mentioning the topic. Reply with only 'yes' or 'no'."},
                {"role": "user", "content": f"Question: {question}\n\nQuote: {quote}\n\nDoes the quote directly answer the question?"},
            ],
            temperature=0.0,
        )
        return (r.choices[0].message.content or "").strip().lower().startswith("y")
    except Exception:
        return False


def main():
    golden = json.load(open(GOLDEN_FILE))
    rows = []
    for g in golden:
        t0 = time.time()
        try:
            d = search(g["question"], g.get("history"))
        except Exception as e:
            rows.append({**g, "error": str(e)})
            print(f"[{g['id']}] ERROR {e}")
            continue
        ms = int((time.time() - t0) * 1000)
        trace, results = d["trace"], d["results"]
        expect = g["expect"]

        has_results = len(results) > 0
        # Abstention correctness: abstain-labeled must be empty; others must not be.
        abstained = not has_results
        abstain_ok = (abstained == (expect == "abstain"))

        # Exact-discourse retrieval: when a user names a specific discourse, that
        # discourse must come back FIRST. `exact_only` is the stronger form —
        # it came back alone, with no near-misses padding the list. First is the
        # gate; only is tracked because a single confident hit is the ideal
        # outcome for a question that names its own answer.
        exact_ok = exact_only = None
        if expect == "exact":
            want = norm_title(g.get("expect_title", ""))
            got = norm_title(results[0]["title"]) if has_results else ""
            exact_ok = bool(want) and got == want
            exact_only = exact_ok and len(results) == 1
        # Route match: structured-labeled must take the structured route;
        # listing-labeled must take the listing route (ordered enumeration).
        if expect == "structured":
            route_ok = trace.get("route") == "structured"
        elif expect == "listing":
            route_ok = trace.get("route") == "listing"
        else:
            route_ok = True
        # An exact-discourse question may be served by the listing route, the
        # exact-phrase shortcut, or semantic search — the route is not the
        # contract, landing on the right discourse is. So route_ok stays True.
        # Quote judged only for questions that should answer and did. Listing
        # results carry a chapter PREVIEW (opening sentences), not an answering
        # quote, so they are deliberately not quote-judged.
        quote = results[0].get("best_sentence") if has_results else None
        quote_ok = judge_quote_answers(g["question"], quote) if (expect in ("answer", "structured") and has_results) else None

        rows.append({
            "id": g["id"], "question": g["question"], "expect": expect,
            "intent": trace.get("intent"), "route": trace.get("route"),
            "n": len(results), "quality": trace.get("quality"),
            "abstain_ok": abstain_ok, "route_ok": route_ok, "quote_ok": quote_ok,
            "exact_ok": exact_ok, "exact_only": exact_only,
            "expect_title": g.get("expect_title"),
            "got_title": results[0]["title"] if has_results else None,
            "ms": ms, "reasons": [r["code"] for r in trace.get("reasons", [])],
        })
        mark = "ok" if (abstain_ok and route_ok and quote_ok is not False
                        and exact_ok is not False) else "FLAG"
        print(f"[{g['id']}] {mark:4} expect={expect:10} intent={str(rows[-1]['intent']):12} route={str(rows[-1]['route'])} n={len(results)} quote_ok={quote_ok} {ms}ms")

    ok = [r for r in rows if "error" not in r]
    answered = [r for r in ok if r["quote_ok"] is not None]
    quote_rate = sum(1 for r in answered if r["quote_ok"]) / len(answered) if answered else 0
    abstain_rate = sum(1 for r in ok if r["abstain_ok"]) / len(ok) if ok else 0
    route_rate = sum(1 for r in ok if r["route_ok"]) / len(ok) if ok else 0
    exact_rows = [r for r in ok if r["exact_ok"] is not None]
    exact_rate = sum(1 for r in exact_rows if r["exact_ok"]) / len(exact_rows) if exact_rows else 0
    only_rate = sum(1 for r in exact_rows if r["exact_only"]) / len(exact_rows) if exact_rows else 0
    lat = sorted(r["ms"] for r in ok)
    p50 = lat[len(lat)//2] if lat else 0
    p90 = lat[int(len(lat)*0.9)] if lat else 0

    print("\n=== SCORECARD ===")
    print(f"  quote_answers_rate:      {quote_rate:.0%}  ({sum(1 for r in answered if r['quote_ok'])}/{len(answered)})")
    print(f"  abstention_correctness:  {abstain_rate:.0%}  ({sum(1 for r in ok if r['abstain_ok'])}/{len(ok)})")
    print(f"  route_match:             {route_rate:.0%}")
    if exact_rows:
        print(f"  exact_discourse_first:   {exact_rate:.0%}  "
              f"({sum(1 for r in exact_rows if r['exact_ok'])}/{len(exact_rows)})")
        print(f"  exact_discourse_only:    {only_rate:.0%}  "
              f"({sum(1 for r in exact_rows if r['exact_only'])}/{len(exact_rows)})")
    print(f"  latency p50/p90:         {p50}ms / {p90}ms")

    json.dump({"quote_rate": quote_rate, "abstain_rate": abstain_rate,
               "route_rate": route_rate, "exact_rate": exact_rate,
               "only_rate": only_rate, "p50": p50, "p90": p90, "rows": rows},
              open(OUT_FILE, "w"), indent=2)

    if "--baseline" in sys.argv:
        base = json.load(open(sys.argv[sys.argv.index("--baseline") + 1]))
        print("\n=== VS BASELINE ===")
        print(f"  quote_rate:  {base['quote_rate']:.0%} -> {quote_rate:.0%}")
        print(f"  abstain:     {base['abstain_rate']:.0%} -> {abstain_rate:.0%}")
        if "exact_rate" in base:
            print(f"  exact_first: {base['exact_rate']:.0%} -> {exact_rate:.0%}")
        gate = (quote_rate >= base["quote_rate"]
                and abstain_rate >= base["abstain_rate"]
                and exact_rate >= base.get("exact_rate", 0))
        print(f"  REGRESSION GATE: {'PASS' if gate else 'FAIL — do not ship'}")

    print(f"\nSaved {OUT_FILE}")


if __name__ == "__main__":
    main()
