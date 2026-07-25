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

import json
import sys
import time
import statistics
import urllib.request

from dotenv import load_dotenv

load_dotenv()

from search.config import openai_client, GRADE_MODEL  # noqa: E402

BASE_URL = "http://localhost:8000"
GOLDEN_FILE = "golden_questions.json"
OUT_FILE = "eval_results.json"


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
        # Route match: structured-labeled must take the structured route.
        route_ok = (trace.get("route") == "structured") if expect == "structured" else True
        # Quote judged only for questions that should answer and did.
        quote = results[0].get("best_sentence") if has_results else None
        quote_ok = judge_quote_answers(g["question"], quote) if (expect in ("answer", "structured") and has_results) else None

        rows.append({
            "id": g["id"], "question": g["question"], "expect": expect,
            "intent": trace.get("intent"), "route": trace.get("route"),
            "n": len(results), "quality": trace.get("quality"),
            "abstain_ok": abstain_ok, "route_ok": route_ok, "quote_ok": quote_ok,
            "ms": ms, "reasons": [r["code"] for r in trace.get("reasons", [])],
        })
        mark = "ok" if abstain_ok and route_ok and (quote_ok is not False) else "FLAG"
        print(f"[{g['id']}] {mark:4} expect={expect:10} intent={str(rows[-1]['intent']):12} route={str(rows[-1]['route'])} n={len(results)} quote_ok={quote_ok} {ms}ms")

    ok = [r for r in rows if "error" not in r]
    answered = [r for r in ok if r["quote_ok"] is not None]
    quote_rate = sum(1 for r in answered if r["quote_ok"]) / len(answered) if answered else 0
    abstain_rate = sum(1 for r in ok if r["abstain_ok"]) / len(ok) if ok else 0
    route_rate = sum(1 for r in ok if r["route_ok"]) / len(ok) if ok else 0
    lat = sorted(r["ms"] for r in ok)
    p50 = lat[len(lat)//2] if lat else 0
    p90 = lat[int(len(lat)*0.9)] if lat else 0

    print("\n=== SCORECARD ===")
    print(f"  quote_answers_rate:      {quote_rate:.0%}  ({sum(1 for r in answered if r['quote_ok'])}/{len(answered)})")
    print(f"  abstention_correctness:  {abstain_rate:.0%}  ({sum(1 for r in ok if r['abstain_ok'])}/{len(ok)})")
    print(f"  route_match:             {route_rate:.0%}")
    print(f"  latency p50/p90:         {p50}ms / {p90}ms")

    json.dump({"quote_rate": quote_rate, "abstain_rate": abstain_rate,
               "route_rate": route_rate, "p50": p50, "p90": p90, "rows": rows},
              open(OUT_FILE, "w"), indent=2)

    if "--baseline" in sys.argv:
        base = json.load(open(sys.argv[sys.argv.index("--baseline") + 1]))
        print("\n=== VS BASELINE ===")
        print(f"  quote_rate:  {base['quote_rate']:.0%} -> {quote_rate:.0%}")
        print(f"  abstain:     {base['abstain_rate']:.0%} -> {abstain_rate:.0%}")
        gate = quote_rate >= base["quote_rate"] and abstain_rate >= base["abstain_rate"]
        print(f"  REGRESSION GATE: {'PASS' if gate else 'FAIL — do not ship'}")

    print(f"\nSaved {OUT_FILE}")


if __name__ == "__main__":
    main()
