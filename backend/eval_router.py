"""Eval harness for the plan_search router — labeled queries asserting the
routed intent and the key filters, including the traps where a query LOOKS
structured but is semantic (and vice versa).

Requires OPENAI_API_KEY (one gpt-4o-mini call per case) and uses the live
catalog when Weaviate is reachable (static fallback otherwise):

    source venv/bin/activate
    python eval_router.py
"""

from dotenv import load_dotenv

load_dotenv()

from search.query_planning import plan_search  # noqa: E402

# (message, history, expected_intent, expected_filter_subset)
# expected_filter_subset entries must all match plan.filters exactly;
# filters not listed are unconstrained. "queries" asserts non-empty when True.
CASES = [
    # --- the user's two motivating examples ---
    ("return the first five discourses of the Gita Vahini", None, "listing",
     {"book": "Geeta Vahini", "chapter_start": 1, "chapter_end": 5}),
    ("return all discourses from 1976", None, "listing",
     {"year_start": 1976, "year_end": 1976}),

    # --- book + ordinal variants ---
    ("first 3 chapters of Prema Vahini", None, "listing",
     {"book": "Prema Vahini", "chapter_start": 1, "chapter_end": 3}),
    ("chapter 3 of Dhyana Vahini", None, "listing",
     {"book": "Dhyana Vahini", "chapter_start": 3, "chapter_end": 3}),
    ("show me discourse 10 from Sathya Sai Speaks volume 14", None, "listing",
     {"book": "Sathya Sai Speaks", "volume": 14, "chapter_start": 10, "chapter_end": 10}),
    ("list the chapters of the Geeta Vahini", None, "listing",
     {"book": "Geeta Vahini"}),

    # --- year / date range ---
    ("discourses from the 1970s", None, "listing",
     {"year_start": 1970, "year_end": 1979}),
    ("all discourses between 1990 and 1995", None, "listing",
     {"year_start": 1990, "year_end": 1995}),
    ("Summer Showers discourses from 1976", None, "listing",
     {"book": "Summer Showers", "year_start": 1976, "year_end": 1976}),

    # --- hybrid: filter + topic ---
    ("discourses from 1976 about surrender", None, "hybrid",
     {"year_start": 1976, "year_end": 1976}),
    ("what does the Geeta Vahini say about the nature of the soul?", None, "hybrid",
     {"book": "Geeta Vahini"}),
    ("teachings on devotion from the 1990s", None, "hybrid",
     {"year_start": 1990, "year_end": 1999}),

    # --- location / occasion ---
    ("discourses given in Brindavan", None, "listing", {"location": "brindavan"}),
    ("Dasara discourses", None, "listing", {"occasion": "dasara"}),
    ("Shivaratri discourses from 1965", None, "listing",
     {"occasion": "shivarathri", "year_start": 1965, "year_end": 1965}),
    ("what did Swami say about seva in Kodaikanal?", None, "hybrid",
     {"location": "kodaikanal"}),

    # --- traps: look structured, are semantic ---
    ("what does the Gita teach about karma?", None, "semantic", {}),
    ("discourses about Brindavan's beauty", None, "semantic", {}),
    ("the story of Alexander", None, "semantic", {}),
    ("what is the meaning of Dasara?", None, "semantic", {}),
    ("the first steps on the spiritual path", None, "semantic", {}),

    # --- no-regression: plain semantic, incl. romanized terms ---
    ("controlling the mind", None, "semantic", {}),
    ("mano nigraham", None, "semantic", {}),
    ("how do I deal with anger toward my family?", None, "semantic", {}),

    # --- multi-turn: filter arrives as a follow-up ---
    ("now just the ones from 1976", ["discourses about selfless service"], "hybrid",
     {"year_start": 1976, "year_end": 1976}),
    ("what about in the Geeta Vahini?", ["what is dharma?"], "hybrid",
     {"book": "Geeta Vahini"}),

    # --- graceful handling ---
    ("first five discourses", None, "semantic", {}),  # no book -> nothing to list
]


def run():
    passed, failed = 0, []
    for message, history, want_intent, want_filters in CASES:
        plan = plan_search(message, history)
        problems = []
        if plan.intent != want_intent:
            problems.append(f"intent={plan.intent!r} want {want_intent!r}")
        for key, want in want_filters.items():
            got = plan.filters.get(key)
            if got != want:
                problems.append(f"filters[{key}]={got!r} want {want!r}")
        if want_intent == "semantic" and plan.filters:
            problems.append(f"unexpected filters {plan.filters}")
        if want_intent != "listing" and not plan.queries:
            problems.append("no semantic queries planned")

        if problems:
            failed.append((message, problems, plan))
            print(f"FAIL  {message!r}")
            for p in problems:
                print(f"        {p}")
        else:
            passed += 1
            print(f"pass  {message!r}  -> {plan.intent} {plan.filters}")

    print(f"\n{passed}/{len(CASES)} passed")
    if failed:
        print("\nFailures in detail:")
        for message, problems, plan in failed:
            print(f"  {message!r}\n    got: intent={plan.intent} filters={plan.filters} "
                  f"queries={plan.queries} sort={plan.sort} limit={plan.limit}")
    return len(failed)


if __name__ == "__main__":
    raise SystemExit(run())
