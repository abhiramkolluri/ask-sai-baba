"""Router-only evaluation — the instrument the pipeline was missing.

WHY THIS EXISTS
eval_ragas.py measures END-TO-END outcomes, so a misrouted question and a
retrieval miss are indistinguishable in it. The router decides which machinery
answers a question, and every routing bug found so far surfaced by accident — a
user report, or collateral damage in an outcome metric — never from a test.

This calls plan_queries directly. No server, no retrieval, no grading: it runs in
seconds, so the router prompt can be iterated on without a 15-minute round trip.

THREE THINGS, because they fail independently:

  intent     Did the question get the right intent? Reported PER-INTENT — a
             12-way taxonomy hides its failures inside an aggregate.
  fields     For listing, was `collection` extracted? This is what actually
             flaked in production while the intent stayed correct, so an
             aggregate "routing accuracy" would have missed it completely.
  stability  With --repeat N, does the same question classify the same way every
             time? This is an AMBIGUITY DETECTOR. A question that flips between
             runs is one the prompt does not decide, and that is fixable by
             disambiguating the prompt — measured on this codebase, a 1-in-3
             flake disappeared entirely once explicit contrast examples were
             added. Instability is a prompt bug, not sampling noise to tolerate.

CASES (router_cases.json) come from real traffic and from real bugs, never from
what the router currently does. Two kinds:
  {"q": ..., "intent": "listing"}          exact intent required
  {"q": ..., "not_intent": "unanswerable"} must NOT be this — over-trigger guards
The second kind is the important one: refusing an answerable question is worse
than the bug that motivated the refusal feature.

    venv/bin/python eval_router.py                # single pass
    venv/bin/python eval_router.py --repeat 3     # + stability
    venv/bin/python eval_router.py --only unanswerable
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


import argparse
import collections
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

load_dotenv()

from search.query_planning import plan_queries  # noqa: E402

CASES_FILE = _here("router_cases.json")

# Intents that dispatch IDENTICALLY. pipeline.py branches on meta/out_of_domain/
# unanswerable, on listing, and on factual/named_text/org_doctrine; everything
# else falls through to the semantic route. So a question flipping between
# conceptual and aspect produces the same search and the same answer, and
# flagging it as instability drowns out the flips that really do change routing.
_SAME_ROUTE = {"conceptual", "scenario", "aspect", "comparative", "occasion"}


def _route_of(intent):
    return "semantic" if intent in _SAME_ROUTE else intent

MAX_WORKERS = 6


def classify(q):
    """One router call -> the fields we assert on. Never raises."""
    meta = {}
    try:
        plan_queries(q, None, trace_out=meta)
    except Exception as e:
        return {"intent": f"ERROR:{e}", "filters": {}, "collection": None,
                "list_count": None, "reason": None}
    filters = meta.get("filters") or {}
    return {
        "intent": meta.get("intent"),
        "filters": filters,
        # `collection` is how the older cases name the book; it now arrives as
        # filters.book, so map it here rather than rewriting every case.
        "collection": filters.get("book") or meta.get("book_requested"),
        "book_requested": meta.get("book_requested"),
        "list_count": meta.get("limit"),
        "reason": meta.get("unanswerable_reason"),
    }


def check(case, got):
    """Return (ok, detail). A case asserts either `intent` or `not_intent`,
    plus optional field expectations."""
    problems = []
    want, got_i = case.get("intent"), got["intent"]
    if want and got_i != want:
        problems.append(f"intent {got_i!r} != {want!r}")
    forbid = case.get("not_intent")
    if forbid and got_i == forbid:
        problems.append(f"intent is {forbid!r} (must not be)")
    # Filters the merged router extracts (book / year range / chapter range /
    # location / occasion). Only the keys a case names are checked; anything
    # else the router adds is left alone.
    for key, want in (case.get("filters") or {}).items():
        have = got["filters"].get(key)
        if have == want:
            continue
        # `book` is the one filter with two legitimate spellings. The catalog
        # normally canonicalises it, but when it doesn't the raw name is carried
        # as book_requested and listing.py resolves it — which is what actually
        # reaches the user. Accept either, mirroring the pipeline.
        if key == "book" and have is None and got.get("book_requested"):
            from search.listing import _resolve_book_name
            if (_resolve_book_name(got["book_requested"]) or "").lower() == str(want).lower():
                continue
        # "first five chapters" is equally correct as limit=5 or as
        # chapter_start=1/chapter_end=5; both reach the same five chapters.
        if key in ("chapter_start", "chapter_end") and got["list_count"]:
            span_ok = (key == "chapter_start" and want == 1) or \
                      (key == "chapter_end" and want == got["list_count"])
            if span_ok:
                continue
        problems.append(f"filters[{key}]={have!r} != {want!r}")
    if case.get("no_filters") and got["filters"]:
        problems.append(f"expected no filters, got {got['filters']}")
    if "collection" in case:
        if not got["collection"]:
            problems.append("collection NOT extracted")
        else:
            # The router's job is EXTRACTION; it legitimately echoes the user's
            # spelling ("Gita Vahini"). Normalising to the corpus form ("Geeta
            # Vahini") is listing.py's job, so assert the contract that actually
            # matters — does this question reach the right book.
            #
            # Accept EITHER the raw extraction or its resolution, mirroring
            # listing.py, which only resolves after a direct lookup misses.
            # Resolving unconditionally here is wrong: "Summer Showers 1990"
            # looks up directly but resolves to the shorter "Summer Showers".
            want, raw = case["collection"].lower(), got["collection"]
            if want not in raw.lower():
                from search.listing import _resolve_book_name
                resolved = _resolve_book_name(raw)
                if not resolved or want not in resolved.lower():
                    problems.append(
                        f"collection {raw!r} (resolves to {resolved!r}) "
                        f"!= {case['collection']!r}")
    if "list_count" in case and got["list_count"] != case["list_count"]:
        # Same equivalence the other way: an explicit chapter range of the right
        # width expresses the count without setting `limit`.
        f = got["filters"]
        span = (f.get("chapter_end") - f.get("chapter_start") + 1
                if f.get("chapter_start") is not None and f.get("chapter_end") is not None else None)
        if span != case["list_count"]:
            problems.append(f"list_count {got['list_count']} != {case['list_count']}"
                            f" (and chapter span {span} does not express it either)")
    return (not problems), "; ".join(problems)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1,
                    help="Classify each case N times and report questions that flip.")
    ap.add_argument("--only", help="Only cases whose expected intent matches this.")
    ap.add_argument("--verbose", action="store_true", help="Print every case, not just failures.")
    args = ap.parse_args()

    cases = json.load(open(CASES_FILE))
    if args.only:
        cases = [c for c in cases
                 if c.get("intent") == args.only or c.get("not_intent") == args.only]
    print(f"{len(cases)} cases x {args.repeat} run(s)\n")

    # Run every (case, repetition) concurrently — the router is a network call, so
    # a 60-case pass is seconds rather than minutes.
    jobs = [(ci, c) for ci, c in enumerate(cases) for _ in range(args.repeat)]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        outs = list(pool.map(lambda j: (j[0], classify(j[1]["q"])), jobs))

    by_case = collections.defaultdict(list)
    for ci, got in outs:
        by_case[ci].append(got)

    failures, unstable = [], []
    per_intent = collections.defaultdict(lambda: [0, 0])  # expected -> [ok, total]

    for ci, case in enumerate(cases):
        runs = by_case[ci]
        seen = collections.Counter(_route_of(r["intent"]) for r in runs)
        if len(seen) > 1:
            unstable.append((case, dict(seen)))
        # Judge the modal run: a case that is right 2/3 of the time still counts
        # as a pass on accuracy, and shows up separately as unstable.
        modal = seen.most_common(1)[0][0]
        got = next(r for r in runs if _route_of(r["intent"]) == modal)
        ok, detail = check(case, got)

        # Filter-only cases assert extraction, not intent, so they group under
        # their own label rather than crashing the per-intent scorecard.
        if case.get("intent"):
            label = case["intent"]
        elif case.get("not_intent"):
            label = f"not:{case['not_intent']}"
        elif case.get("filters"):
            label = "filters"
        else:
            label = "no-filters"
        per_intent[label][1] += 1
        if ok:
            per_intent[label][0] += 1
        else:
            failures.append((case, got, detail))
        if args.verbose:
            print(f"  {'ok  ' if ok else 'FAIL'} {str(got['intent']):14s} | {case['q'][:56]}")

    total = len(cases)
    passed = total - len(failures)
    print(f"\n=== ROUTER SCORECARD ===")
    print(f"  accuracy:   {passed/total:.0%}  ({passed}/{total})")
    print(f"  stability:  {(total-len(unstable))/total:.0%}  "
          f"({len(unstable)} question(s) classified inconsistently)")

    print("\n  by expected intent:")
    for label, (ok, tot) in sorted(per_intent.items(), key=lambda kv: kv[1][0] / kv[1][1]):
        bar = "" if ok == tot else "   <-- "
        print(f"    {label:22s} {ok}/{tot}{bar}")

    if failures:
        print(f"\n=== {len(failures)} FAILURE(S) ===")
        for case, got, detail in failures:
            print(f"  {case['q'][:66]!r}")
            print(f"     {detail}")
            if case.get("note"):
                print(f"     context: {case['note']}")

    if unstable:
        print(f"\n=== {len(unstable)} UNSTABLE (prompt ambiguity) ===")
        for case, seen in unstable:
            print(f"  {case['q'][:60]!r} -> {seen}")
        print("  These are questions the prompt does not decide. Fix by adding an "
              "explicit contrast example, not by changing sampling.")

    json.dump({"accuracy": passed / total, "total": total, "passed": passed,
               "unstable": len(unstable),
               "failures": [{"q": c["q"], "detail": d} for c, _, d in failures]},
              open(_artifact("router_eval_results.json"), "w"), indent=2)

    print(f"\nSaved router_eval_results.json")
    return 1 if (failures or unstable) else 0


if __name__ == "__main__":
    sys.exit(main())
