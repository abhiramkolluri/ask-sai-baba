"""Keyword-route unit tests — the gate and the lexical route, no network.

Two things fail independently and are tested separately:

  is_keyword_query   Does a bare topic word take the route, and does a QUESTION
                     stay off it? The cost of a false positive here is real: a
                     question routed to lexical matching loses the semantic
                     search that was the right tool for it.
  keyword_search     Given BM25 candidates, does it keep only passages that
                     LITERALLY contain the term, order titles first, normalize
                     the score, and fall through when the corpus is thin?

Run: venv/bin/python evals/test_keyword_route.py
"""

import os as _os, sys as _sys
# This script lives in a subdirectory but imports the backend's top-level
# modules (search, weaviate_client, …), so put the backend root on sys.path
# before those imports. Keeps the script runnable from anywhere.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from dotenv import load_dotenv; load_dotenv()

from search import keyword
from search.query_planning import is_keyword_query

fails = []
def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        fails.append(name)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

print("[1] bare topic words take the keyword route")
for q, expected in [("karma", "karma"),
                    ("Truth", "Truth"),
                    ("inner peace", "inner peace"),
                    ("the truth", "the truth"),          # 2 RAW tokens, stopword included
                    ("  karma  ", "karma"),              # whitespace normalized
                    ("Easwaramma", "Easwaramma"),
                    ("karma.", "karma")]:                # trailing punctuation stripped
    check(f"{q!r} -> {expected!r}", is_keyword_query(q) == expected)

print("[2] questions and longer phrases stay on the semantic route")
for q in ["what is karma",
          "importance of truth",       # 3 raw tokens — the aspect is the point
          "is karma real",
          "how truth",                 # 2 tokens but interrogative
          "karma?",                    # the user told us they asked something
          "tell me",
          "love all serve all",
          "",
          "   "]:
    check(f"{q!r} -> None", is_keyword_query(q) is None)

print("[2b] greetings and junk are NOT topics — the router abstains on these")
# Regression guard: these are short and declarative, so rules 1-2 pass them, and
# the corpus really does contain the words — bare "test" returned 10 discourses
# led by "Welcome The Tests" before _NON_TOPICAL existed, against golden-set
# labels (q034/q035) that expect an abstention.
for q in ["Hello", "hi", "test", "Testing", "thanks", "ok", "namaste",
          "sai ram", "asdf", "help", "bye", "good morning"]:
    check(f"{q!r} -> None", is_keyword_query(q) is None)


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------

def passage(article_id, content, title="Some Discourse", score=1.0):
    """A Passage row in the shape retrieval._passage_row emits."""
    return {"_id": f"p-{article_id}-{abs(hash(content)) % 1000}", "article_id": article_id,
            "chunk_index": 0, "content": content, "title": title, "location": "",
            "occasion": "", "link": "", "collection_name": "X", "date_authored": "",
            "score": score}


def stub_bm25(rows):
    """Force keyword_search to see exactly these candidates, no Weaviate, and
    skip the reranker call select_best_sentences would make."""
    keyword.search_passages_keyword = lambda term, overfetch=None: list(rows)
    keyword.select_best_sentences = lambda q, d: d


print("[3] BM25 hits that don't literally contain the term are dropped")
stub_bm25([
    passage("a1", "He spoke of karma and its fruits.", score=9.0),
    passage("a2", "The law of karma is inexorable.", score=8.0),
    passage("a3", "Duty and action shape a life.", score=7.0),   # no 'karma' — bag-of-words hit
    passage("a4", "Karma yoga is the path of action.", score=6.0),
])
r = keyword.keyword_search("karma", limit=10)
check("status == hit", r["status"] == "hit")
check("only literal passages kept", r["literal_passages"] == 3)
check("non-literal discourse excluded", "a3" not in [d["_id"] for d in r["discourses"]])

print("[4] a title match outranks a higher BM25 score in the body")
stub_bm25([
    passage("a1", "Karma is mentioned here once.", title="On Duty", score=9.0),
    passage("a2", "It is mentioned here too.", title="The Path of Karma", score=1.0),
    passage("a3", "And karma here as well.", title="Another", score=5.0),
])
r = keyword.keyword_search("karma", limit=10)
check("title match is first", r["discourses"][0]["title"] == "The Path of Karma")

print("[5] scores are normalized into 0-1, not raw BM25")
stub_bm25([passage(f"a{i}", "About karma.", score=s) for i, s in enumerate([9.0, 6.0, 3.0])])
r = keyword.keyword_search("karma", limit=10)
check("all scores within 0-1", all(0.0 <= d["score"] <= 1.0 for d in r["discourses"]))
check("top score is 1.0", max(d["score"] for d in r["discourses"]) == 1.0)

print("[6] fewer than KEYWORD_MIN_DISCOURSES distinct discourses -> thin, fall through")
# Four passages, but only two discourses: a common word stacking passages from
# the same article is a thin result dressed up as a thick one.
stub_bm25([
    passage("a1", "Vairagya is detachment.", score=9.0),
    passage("a1", "More on vairagya here.", score=8.0),
    passage("a1", "Still vairagya.", score=7.0),
    passage("a2", "Vairagya again.", score=6.0),
])
r = keyword.keyword_search("vairagya", limit=10)
check("status == thin", r["status"] == "thin")
check("no discourses returned", r["discourses"] == [])
check("literal count still reported", r["literal_passages"] == 4)

print("[7] zero BM25 candidates -> thin, not a crash")
stub_bm25([])
r = keyword.keyword_search("zzzznotaword", limit=10)
check("status == thin", r["status"] == "thin")

print("[8] multi-word terms need the whole phrase, not either word")
stub_bm25([
    passage("a1", "He described inner peace at length.", score=9.0),
    passage("a2", "Peace of the world begins within.", score=8.0),   # 'peace' only
    passage("a3", "The inner voice is the guide.", score=7.0),       # 'inner' only
    passage("a4", "Inner peace, once found, stays.", score=6.0),     # comma between
])
r = keyword.keyword_search("inner peace", limit=10)
check("only whole-phrase matches kept", r["literal_passages"] == 2)

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
_sys.exit(1 if fails else 0)
