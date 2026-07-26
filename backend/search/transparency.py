"""Pipeline transparency — collect a user-facing trace of how a search ran.

The search pipeline (plan -> retrieve -> rerank -> fuse -> grade -> aggregate)
computes many intermediate signals (planned facets, per-facet candidate counts,
which reranker ran, how many passages the grader kept vs. rejected, timings) that
were previously discarded. This module provides the small, dependency-free
plumbing to gather those signals into a plain dict that ``search_browse`` returns
alongside its results and the frontend renders as a "How I searched" panel.

Design rules baked in here:
  - The trace is a plain dict (jsonify-friendly, matches the package's style).
  - It NEVER contains passage bodies — only titles, facets, scores, reasons — so a
    worst-case trace stays a few KB and is cheap to persist with a chat message.
  - Every section is optional: the exact-phrase shortcut emits a minimal trace,
    and the grader-fallback path emits a partial one. Consumers must treat every
    field as possibly-absent.
  - ``assess_quality`` derives a coarse ``quality`` label plus machine-readable
    ``reasons`` codes; the human-facing copy for those codes lives in the frontend
    so wording can iterate without a backend redeploy.
"""

import time

from .config import (
    QUALITY_STRONG_MIN_RESULTS,
    QUALITY_STRONG_MIN_RELEVANCE,
)

TRACE_VERSION = 1

# Romanized-term spellings the corpus uses, keyed by common user spellings. Used
# to emit a SPELLING_HINT when a weak/empty result contains a term the user likely
# misspelled relative to the corpus. Keys are lowercase user spellings; values are
# the corpus form to suggest. The discourse corpus consistently uses "Geetha",
# "Sathya", etc. (English-transliteration of the Telugu/Sanskrit).
CORPUS_SPELLINGS = {
    "gita": "Geetha",
    "geeta": "Geetha",
    "geetha": "Geetha",
    "bhagavadgita": "Bhagavad Geetha",
    "satya": "Sathya",
    "moksa": "moksha",
    "ahinsa": "ahimsa",
    "krisna": "Krishna",
    "krsna": "Krishna",
    "atma": "Atma",
    "dharma": "dharma",
    "karma": "karma",
}


def new_trace(query, history=None):
    """Return an empty trace skeleton for one search. Sections are filled in by
    ``search_browse`` as each stage runs; anything a given path skips stays at its
    default so the frontend can guard on presence/emptiness.
    """
    history = history or []
    return {
        "version": TRACE_VERSION,
        "query": query,
        "history_used": len([h for h in history if isinstance(h, str) and h.strip()]),
        "exact_phrase": {"phrase": None, "matched": False},
        "planning": {"facets": [], "fallback": False},
        # Set by search_browse when the planner routed an occasion question to a
        # metadata filter: {"occasion", "applied", "fell_back"}. None otherwise.
        "metadata_filter": None,
        # True when every retrieval failed to reach the search service — an
        # infrastructure error, distinct from a genuinely empty result.
        "service_error": False,
        # True when at least one facet fell back to hybrid-score order because
        # reranking didn't run (missing key, rate limit, timeout). Results are
        # still real, just ordered less well. Surfaced because a rate-limited
        # key previously degraded 28% of searches with no signal at all.
        "rerank_degraded": False,
        # True when this result came from the repeated-question cache; the
        # timings below are the ORIGINAL run's, not this request's.
        "cached": False,
        # True for phase 1 of a two-phase search: candidates are reranked but NOT
        # graded, so quotes are absent and quality is "pending" until the client
        # posts `pending_passage_ids` to /search/verify.
        "deferred": False,
        "pending_passage_ids": [],
        # Router v2 fields. `intent` is the classified question type (see
        # query_planning.KNOWN_INTENTS); `route` is which strategy handled it
        # ("semantic" | "structured" | "guidance"); `is_comparison` marks
        # compare/which-is-better questions; `entities` are named things detected.
        "intent": None,
        "route": None,
        "is_comparison": False,
        "entities": [],
        # The router's one-sentence explanation of why THIS question cannot be
        # answered from the discourses. Only set for intent="unanswerable"; None
        # when the router produced nothing usable, in which case the frontend
        # falls back to static copy.
        "unanswerable_reason": None,
        # Set by the structured route when a matched entity is a known corpus gap
        # (e.g. a named text the discourses don't cover) -> honest abstention.
        "kb_gap": False,
        # Set by the listing route: {collection, count, order, not_found}.
        "listing": None,
        "retrieval": {"facet_results": [], "merged_candidates": 0},
        "grading": {
            "model": None,
            "fallback": False,
            "kept": 0,
            "rejected": 0,
            "kept_passages": [],
            "rejected_passages": [],
        },
        "results": {"discourses": 0, "top_relevance": 0.0},
        "quality": "strong",
        "reasons": [],
        "timings_ms": {},
    }


class StageTimer:
    """Context manager that accumulates wall-clock time (ms) for a pipeline stage
    into ``trace["timings_ms"][stage]``. Accumulating because retrieval/rerank run
    once per facet in a loop — entering the same stage repeatedly sums the deltas.

    When ``trace`` is None (tracing not requested) this is a no-op, so pipeline
    code can wrap every stage in ``with StageTimer(trace, ...)`` unconditionally
    and stay readable whether or not a trace is being collected.
    """

    def __init__(self, trace, stage):
        self.trace = trace
        self.stage = stage
        self._start = None

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.trace is None:
            return False
        elapsed_ms = int((time.perf_counter() - self._start) * 1000)
        timings = self.trace.setdefault("timings_ms", {})
        timings[self.stage] = timings.get(self.stage, 0) + elapsed_ms
        return False


def _query_tokens(query):
    """Lowercase alphabetic tokens of a query, for spelling-hint matching."""
    if not query or not isinstance(query, str):
        return []
    return [t for t in "".join(c if c.isalpha() else " " for c in query).lower().split() if t]


def assess_quality(trace, results):
    """Set ``trace['quality']`` and ``trace['reasons']`` from the collected trace.

    quality:
      - "none"    : no results at all
      - "strong"  : enough results AND a confidently-relevant top result
      - "partial" : everything in between
    reasons are emitted only when quality != "strong", in priority order, as
    ``{"code": ..., "data": {...}?}`` objects the frontend maps to copy.
    """
    # Infrastructure failure short-circuits everything: it is neither empty nor
    # weak, it is "we couldn't complete the search" and the user should retry.
    if trace.get("service_error"):
        trace["quality"] = "error"
        trace["reasons"] = [{"code": "SERVICE_UNAVAILABLE"}]
        return trace

    num = len(results)
    top_rel = trace["results"].get("top_relevance", 0.0) or 0.0

    # Phase 1 of a two-phase search: nothing has been graded yet, so any verdict
    # here would be guesswork on a reranker score that isn't on the same scale as
    # grade relevance. Say "pending" and let phase 2 assess for real — quality
    # copy shown now and contradicted seconds later is worse than none.
    if trace.get("deferred"):
        trace["quality"] = "pending"
        trace["reasons"] = []
        return trace

    if num == 0:
        trace["quality"] = "none"
    elif (trace.get("exact_phrase", {}).get("matched")
          or trace.get("route") == "structured"
          or trace.get("route") == "listing"):
        # A verified exact-phrase match, a canonical entity lookup, OR an ordered
        # collection listing is a precise outcome regardless of count — one
        # canonical discourse, or 5 requested chapters, is a strong result.
        trace["quality"] = "strong"
    elif num >= QUALITY_STRONG_MIN_RESULTS and top_rel >= QUALITY_STRONG_MIN_RELEVANCE:
        trace["quality"] = "strong"
    else:
        trace["quality"] = "partial"

    # Some notes are shown regardless of quality (unlike the weak-result reasons
    # below), because they're about the KIND of question, not a retrieval miss:
    #  - factual/biographical -> discourse search matches themes, not facts;
    #  - comparative -> we surface both sides but don't compose a comparison;
    #  - meta -> a request to the product, not the corpus.
    base_reasons = []
    intent = trace.get("intent")
    if intent == "factual":
        base_reasons.append({"code": "FACTUAL_QUESTION"})
    if trace.get("is_comparison"):
        base_reasons.append({"code": "COMPARISON_BOTH_SIDES"})
    # Degraded ranking is reported even on "strong" results: the discourses are
    # genuine, but their ORDER is only hybrid score, so the top result is less
    # trustworthy than usual. Silence here is what hid 294 rate-limit failures.
    if trace.get("rerank_degraded"):
        base_reasons.append({"code": "RERANK_DEGRADED"})

    # Listing route couldn't find the named collection -> abstain honestly.
    if (trace.get("listing") or {}).get("not_found"):
        col = trace["listing"].get("collection")
        trace["reasons"] = [{"code": "LISTING_NOT_FOUND", "data": {"collection": col}}]
        return trace

    # Structured route determined the corpus doesn't cover this entity -> abstain
    # honestly with a single clean note (not a pile of refinement tips).
    if trace.get("kb_gap"):
        entity = (trace.get("entities") or [None])[0]
        trace["reasons"] = [{"code": "KB_KNOWN_GAP", "data": {"entity": entity}}]
        return trace

    # Meta / out-of-domain questions were short-circuited before retrieval, so the
    # retrieval-miss heuristics below (MULTI_TOPIC_DILUTION, etc.) don't apply —
    # give them a single clean reason instead of a pile of irrelevant tips.
    # Unanswerable: the subject is in this corpus's world but the question is not
    # something any discourse answers (a ranking nobody made, our opinion, or a
    # prediction about one person). One clean note carrying the router's own
    # sentence — NOT a pile of refinement tips, because the question is not
    # malformed and telling someone to rephrase it would be wrong.
    if intent == "unanswerable":
        trace["reasons"] = [{
            "code": "UNANSWERABLE",
            "data": {"reason": trace.get("unanswerable_reason")},
        }]
        return trace

    if intent == "meta":
        trace["reasons"] = [{"code": "META_REQUEST"}]
        return trace
    if intent == "out_of_domain":
        trace["reasons"] = base_reasons + ([{"code": "NO_MATCHES"}] if len(results) == 0 else [])
        return trace

    if trace["quality"] == "strong":
        trace["reasons"] = base_reasons
        return trace

    reasons = list(base_reasons)
    exact = trace.get("exact_phrase", {})
    grading = trace.get("grading", {})
    retrieval = trace.get("retrieval", {})
    facets = trace.get("planning", {}).get("facets", [])
    merged = retrieval.get("merged_candidates", 0)

    # (1) Exact-phrase requested but not found -> we searched its meaning instead.
    if exact.get("phrase") and not exact.get("matched"):
        reasons.append({"code": "EXACT_PHRASE_MISS", "data": {"phrase": exact.get("phrase")}})

    # (2) Retrieval surfaced nothing at all. Requires no results too, so the
    #     exact-phrase shortcut (which produces results without running retrieval,
    #     leaving merged at 0) doesn't trip this.
    if merged == 0 and num == 0:
        reasons.append({"code": "NO_MATCHES"})

    # (3) Candidates existed but the grader rejected all of them (real judgment,
    #     not the fallback path where nothing is judged).
    if merged > 0 and grading.get("kept", 0) == 0 and not grading.get("fallback"):
        reasons.append({"code": "ALL_REJECTED_BY_GRADER"})

    # (4) The message spanned several distinct concepts, diluting each search.
    if len(facets) >= 3 or (len(facets) >= 2 and trace["quality"] in ("none", "partial")):
        reasons.append({"code": "MULTI_TOPIC_DILUTION", "data": {"count": len(facets), "facets": facets}})

    # (5) We have results, but none is confidently relevant.
    if num > 0 and top_rel < QUALITY_STRONG_MIN_RELEVANCE:
        reasons.append({"code": "LOW_RELEVANCE"})

    # (6) A query term is likely misspelled relative to the corpus.
    for tok in _query_tokens(trace.get("query", "")):
        corpus_form = CORPUS_SPELLINGS.get(tok)
        if corpus_form and corpus_form.lower() != tok:
            reasons.append({"code": "SPELLING_HINT", "data": {"user_term": tok, "corpus_term": corpus_form}})
            break

    # (7) The planner crashed and we searched the raw message verbatim.
    if trace.get("planning", {}).get("fallback"):
        reasons.append({"code": "PLANNER_FALLBACK"})

    trace["reasons"] = reasons
    return trace
