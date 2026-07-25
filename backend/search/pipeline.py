"""Top-level orchestration — wire the pipeline stages into public entrypoints.

This module is the conductor: it calls query planning, then per-facet retrieval +
reranking, fuses the lists, runs the extractive grader, and aggregates to
citations. The heavy lifting lives in the stage modules
(``query_planning`` / ``retrieval`` / ``ranking``); here we only sequence them.

Public entrypoints:
  - ``search_browse``      — the main discourse search used by ``/search`` and chat.
  - ``generate_followups`` — propose + verify follow-up questions for an answer.
  - ``search``             — thin compatibility wrapper over ``search_browse``.
  - ``extract_quoted_phrase`` / ``format_docs`` — small query/format helpers used
    by the chat layer.
"""

import re
import json
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any

from .config import (
    openai_client,
    GRADE_MODEL,
    PASSAGE_OVERFETCH,
    RERANK_KEEP,
    MERGE_MIN_PER_FACET,
    MERGE_PER_FACET_CAP,
    FACET_MAX_WORKERS,
    ROUTER_V2_ENABLED,
    CHAT_FALLBACK_DISCOURSES,
    GRADE_MIN_RELEVANCE,
    FOLLOWUP_CANDIDATES,
    FOLLOWUP_KEEP,
    FOLLOWUP_OVERFETCH,
    FOLLOWUP_RERANK_KEEP,
    FOLLOWUP_MIN_HITS,
    FOLLOWUP_MAX_WORKERS,
)
from .query_planning import plan_queries
from .retrieval import search_passages, search_exact, _rrf_merge, phrase_in_text
from .ranking import (
    rerank_passages,
    grade_and_quote_passages,
    aggregate_to_discourses,
    select_best_sentences,
)
from .transparency import new_trace, StageTimer, assess_quality
from .resilience import PipelineServiceError
from .knowledge import lookup_entity
from . import cache


# ===========================================================================
# Small query / formatting helpers
# ===========================================================================

def extract_quoted_phrase(query: str):
    """
    Extract the first double-quoted phrase from a user query string.

    If the user's message contains a phrase wrapped in double quotes,
    this function returns that phrase in lowercase with surrounding
    whitespace stripped. If no quoted phrase is found, returns None.

    Examples:
        'retrieve the discourse on "Love and Truth"' -> 'love and truth'
        '"Duty and Devotion"'                        -> 'duty and devotion'
        'tell me about love and truth'               -> None

    Args:
        query: The raw user query string, which may contain quoted substrings.

    Returns:
        The first quoted phrase as a lowercase string, or None if not found.
    """
    if not query or not isinstance(query, str):
        return None
    match = re.search(r'"([^"]+)"', query)
    if match:
        return match.group(1).strip().lower()
    return None

def format_docs(docs):
    """Format documents for context."""
    formatted_docs = []
    if not docs:
        logging.warning("No documents provided to format_docs")
        return "From discourse 'General Spiritual Guidance': This discourse provides general spiritual guidance and wisdom from Sai Baba's teachings."

    for doc in docs:
        if isinstance(doc, dict):
            title = doc.get('title', 'Untitled')
            # Prefer the vetted matched passage in full — it is already short
            # (post-chunking) and was confirmed relevant by the grader. Only
            # fall back to truncating raw `content` when no passage is present
            # (e.g. exact-match full-article results).
            matched_passage = doc.get('matched_passage')
            if matched_passage:
                excerpt = str(matched_passage)
            else:
                excerpt = str(doc.get('content', 'N/A'))[:1500]
            formatted_docs.append(f"From discourse '{title}': {excerpt}")

    if not formatted_docs:
        return "From discourse 'General Spiritual Guidance': This discourse provides general spiritual guidance and wisdom from Sai Baba's teachings."

    return "\n\n".join(formatted_docs)


# ===========================================================================
# Main discourse search — plan → retrieve → rerank → fuse → grade → aggregate
# ===========================================================================

def search_browse(query: str, limit: int = 5, exact_phrase: str = None, allow_empty: bool = True, history=None, return_trace: bool = False):
    """Search discourses via the passage pipeline (hybrid -> rerank -> grade -> aggregate).

    Keeps the quoted-phrase exact-match shortcut from the legacy implementation.
    `allow_empty` controls the no-results behavior:
      - True (browse): return [] when nothing directly answers the query.
      - False (chat): fall back to the top reranked passages so the generator
        still has grounding context.

    `return_trace` (opt-in): when True, returns a ``(results, trace)`` tuple where
    ``trace`` is the transparency dict (see search/transparency.py) describing how
    the search ran. When False (default, all existing callers), returns just the
    results list as before.

    The original near_text implementation is preserved verbatim as
    search_browse_articles_legacy() for rollback.
    """
    # When return_trace is False, `trace` stays None and every trace write below is
    # skipped; StageTimer is a no-op on a None trace, so the traced and untraced
    # paths are the same code with no branching noise. `trace["planning"]` etc. are
    # pre-created sub-dicts (see new_trace), so we can hand them straight to the
    # stage functions as `trace_out` without None checks. plan_meta is always a
    # real dict (throwaway when untraced) because the planner reports the occasion
    # filter through it and occasion routing must work for chat too.
    # Repeated-question cache (Phase 4, off by default). Keyed on the original
    # args before `query` is reassigned by the exact-phrase branch. A hit returns
    # the exact prior value; a miss/disabled cache returns None.
    cache_args = (query, history, exact_phrase, allow_empty, return_trace)
    cached = cache.get(*cache_args)
    if cached is not None:
        return cached

    trace = new_trace(query, history) if return_trace else None
    plan_meta = trace["planning"] if trace else {}
    grade_meta = trace["grading"] if trace else None

    def _finish(results):
        """Record result-level fields + quality on the trace (if any), cache the
        value, and return in the caller's requested shape: (results, trace) when
        tracing, else results."""
        if trace is None:
            value = results
        else:
            trace["results"]["discourses"] = len(results)
            trace["results"]["top_relevance"] = results[0].get("score", 0.0) if results else 0.0
            assess_quality(trace, results)
            value = (results, trace)
        cache.put(*cache_args, value)
        return value

    with StageTimer(trace, "total"):
        # (a) Quoted-phrase shortcut: try exact match first. search_exact's
        # Weaviate filters are token-based and can return bag-of-words matches
        # that never contain the phrase, so each hit is verified with
        # phrase_in_text before we honor the "exact match" claim.
        if exact_phrase:
            if trace:
                trace["exact_phrase"]["phrase"] = exact_phrase
            exact_results = search_exact(exact_phrase, limit=limit)
            exact_results = [
                r for r in exact_results
                if phrase_in_text(exact_phrase, r.get("title", ""))
                or phrase_in_text(exact_phrase, r.get("content", ""))
            ]
            if exact_results:
                if trace:
                    trace["exact_phrase"]["matched"] = True
                # Normalize to the pipeline output shape so downstream consumers
                # (format_docs, frontend) see matched_passage/passage_index.
                for r in exact_results:
                    r.setdefault("matched_passage", r.get("content", ""))
                    r.setdefault("passage_index", 0)
                return _finish(select_best_sentences(query, exact_results))
            # (b) No exact match — fall through using the phrase as the query, but
            # route into the passage pipeline instead of near_text.
            query = exact_phrase

        # (c) Plan the query into 1-N standalone sub-queries (multi-turn + distillation
        #     + adaptive decomposition), retrieve and rerank EACH against its own facet,
        #     then fuse with provenance so quotes can be judged per-facet downstream.
        with StageTimer(trace, "planning"):
            planned = plan_queries(query, history, trace_out=plan_meta)
        if trace:
            trace["planning"]["facets"] = planned

        # The planner flags occasion questions ("discourses given during Dasara");
        # retrieval then filters on the passage `occasion` metadata instead of
        # hoping semantic search lands on the right occasions.
        occasion = plan_meta.get("occasion")
        intent = plan_meta.get("intent")
        # Lift the router fields to the top-level trace where assess_quality and
        # the frontend read them.
        if trace:
            trace["intent"] = intent
            trace["is_comparison"] = bool(plan_meta.get("is_comparison"))
            trace["entities"] = plan_meta.get("entities") or []

        # (c.1) Router v2 dispatch. "meta" (a request to the product, e.g. "give me
        # some follow ups") and "out_of_domain" (unrelated/gibberish) can't be
        # answered from the discourse corpus, so short-circuit to an honest empty
        # result + guidance rather than semanticizing them into spurious matches.
        # Other intents fall through to the semantic route here; the structured
        # knowledge route (factual/named_text/org_doctrine) is wired in Phase 2.
        if ROUTER_V2_ENABLED and intent in ("meta", "out_of_domain"):
            if trace:
                trace["route"] = "guidance"
            return _finish([])

        # (c.2) Structured knowledge route: factual/named-text/org-doctrine
        # questions are looked up in the Entity collection instead of being
        # semanticized. A "hit" returns canonical discourses; a "gap" abstains
        # honestly; a "miss" falls through to the semantic route below.
        if ROUTER_V2_ENABLED and intent in ("factual", "named_text", "org_doctrine"):
            with StageTimer(trace, "knowledge"):
                kb = lookup_entity(query, plan_meta.get("entities"))
            if kb["status"] == "hit":
                if trace:
                    trace["route"] = "structured"
                    if kb["entity"]:
                        trace["entities"] = [kb["entity"]]
                return _finish(kb["results"])
            if kb["status"] == "gap":
                if trace:
                    trace["route"] = "structured"
                    trace["kb_gap"] = True
                    if kb["entity"]:
                        trace["entities"] = [kb["entity"]]
                return _finish([])
            # miss -> fall through to semantic

        if trace:
            trace["route"] = "semantic"

        def _retrieve_one(q, occ):
            """Retrieve + rerank a single facet. Runs concurrently across facets,
            so it times itself into its own stats row rather than a shared trace
            stage (concurrent per-stage sums would overstate real latency).

            A PipelineServiceError (retrieval couldn't reach Weaviate) is caught
            here and marked `errored` so one flaky facet doesn't kill a
            multi-facet query; search_browse decides the whole search failed only
            if EVERY facet errored (distinguishing infra failure from empty)."""
            started = time.perf_counter()
            try:
                cands = search_passages(q, PASSAGE_OVERFETCH, occasion=occ)
            except PipelineServiceError as e:
                logging.error(f"facet retrieval errored for {q!r}: {e}")
                return [], {"facet": q, "retrieved": 0, "kept_after_rerank": 0,
                            "reranker": "n/a", "ms": int((time.perf_counter() - started) * 1000),
                            "errored": True}
            rl = rerank_passages(q, cands, RERANK_KEEP)
            for p in rl:
                p["source_query"] = q
            # Which reranker actually ran, inferred without touching rerank_passages:
            # the Cohere path is the only one that sets `rerank_score`; both the
            # no-key and exception fallbacks omit it -> hybrid-score order.
            reranker = "n/a" if not rl else ("cohere" if "rerank_score" in rl[0] else "hybrid_score")
            stat = {
                "facet": q,
                "retrieved": len(cands),
                "kept_after_rerank": len(rl),
                "reranker": reranker,
                "ms": int((time.perf_counter() - started) * 1000),
                "errored": False,
            }
            return rl, stat

        def _retrieve_and_rerank(occ):
            """One retrieval+rerank pass over every facet, fanned out one worker
            per facet (facets are independent; sequential passes pushed 3-4 facet
            queries past API Gateway's 29s production timeout). Returns per-facet
            ranked lists plus stats, in facet order. Stats are recorded into the
            trace only for the pass whose results we keep — the occasion-filtered
            pass may be discarded and re-run unfiltered. The single trace timing
            "retrieval" is the wall clock of the whole fan-out, rerank included.
            """
            with StageTimer(trace, "retrieval"):
                if len(planned) == 1:
                    pairs = [_retrieve_one(planned[0], occ)]
                else:
                    with ThreadPoolExecutor(max_workers=min(len(planned), FACET_MAX_WORKERS)) as pool:
                        pairs = list(pool.map(lambda q: _retrieve_one(q, occ), planned))
            return [rl for rl, _ in pairs], [stat for _, stat in pairs]

        ranked_lists, facet_stats = _retrieve_and_rerank(occasion)
        fell_back = False
        if occasion and sum(s["retrieved"] for s in facet_stats) < 5:
            # The occasion filter found (almost) nothing — the occasion name may
            # not match corpus metadata. Retry unfiltered rather than returning a
            # hollow result for a real topic; the trace records the fallback so
            # the UI can say so honestly.
            fell_back = True
            ranked_lists, facet_stats = _retrieve_and_rerank(None)

        # Record per-facet stats first so the trace is complete even when we
        # short-circuit on a service error below.
        if trace:
            trace["retrieval"]["facet_results"] = facet_stats
            if occasion:
                trace["metadata_filter"] = {
                    "occasion": occasion,
                    "applied": not fell_back,
                    "fell_back": fell_back,
                }

        # If EVERY facet failed to reach the search service, this is an
        # infrastructure failure, not an empty corpus. On the traced path (the
        # frontend) surface it honestly — service_error drives a "please try
        # again" state instead of a misleading "no discourses found". The
        # non-traced path (legacy /query chat) falls through to today's empty
        # handling unchanged. One facet erroring is tolerated (handled above).
        if trace and facet_stats and all(s.get("errored") for s in facet_stats):
            trace["service_error"] = True
            return _finish([])

        # Fuse the per-facet lists. The cap scales with facet count and every
        # facet keeps its top passages: with a flat cap, one facet of a
        # multi-topic question could be crowded out of the merge entirely and
        # never reach the grader (observed in adversarial probing).
        merged = _rrf_merge(ranked_lists)
        cap = max(RERANK_KEEP, MERGE_PER_FACET_CAP * len(ranked_lists))
        keep_ids = {p["_id"] for p in merged[:cap]}
        for rl in ranked_lists:
            keep_ids.update(p["_id"] for p in rl[:MERGE_MIN_PER_FACET])
        reranked = [p for p in merged if p["_id"] in keep_ids]
        if trace:
            trace["retrieval"]["merged_candidates"] = len(reranked)

        # Unified extractive grade: judges relevance AND extracts the verbatim answering
        # quote per passage against its own facet, attaching `best_sentence` to each kept one.
        with StageTimer(trace, "grading"):
            graded = grade_and_quote_passages(query, reranked, trace_out=grade_meta)
        results = aggregate_to_discourses(graded, limit)

        # (d) Handle the empty case.
        if not results:
            if allow_empty:
                return _finish([])
            # Chat path: ground the generator on the top reranked (pre-grade)
            # passages even though the grader was not satisfied.
            fallback = aggregate_to_discourses(reranked[:CHAT_FALLBACK_DISCOURSES], CHAT_FALLBACK_DISCOURSES)
            return _finish(select_best_sentences(query, fallback))

        # Quotes are already attached by the unified grade step.
        return _finish(results)

def search(user_query: str, collection=None) -> List[Dict[str, Any]]:
    """Search for documents."""
    return search_browse(user_query)


# ===========================================================================
# Follow-up question generation — propose, then verify against the real pipeline
# ===========================================================================

def _verify_followup(candidate: str):
    """Run a candidate follow-up through a lightweight version of the search
    pipeline and report whether it surfaces directly-answering quotes.

    The candidate is already a standalone question, so plan_queries is skipped and
    it is treated as its own single facet. Returns (top_relevance, hit_count): the
    highest grade_relevance among directly-answering discourses and how many cleared
    the bar. (0.0, 0) means nothing directly answers it -> the candidate is dropped.
    """
    try:
        cands = search_passages(candidate, FOLLOWUP_OVERFETCH)
        reranked = rerank_passages(candidate, cands, FOLLOWUP_RERANK_KEEP)
        graded = grade_and_quote_passages(candidate, reranked)
        # grade_and_quote_passages only keeps passages with a verbatim answering
        # quote and relevance >= GRADE_MIN_RELEVANCE, so anything returned here is a
        # genuine hit. (Its rare fallback path can return ungraded passages without
        # grade_relevance; treat those as not-verified by reading .get default 0.)
        relevances = [p.get("grade_relevance", 0.0) for p in graded]
        relevances = [r for r in relevances if r >= GRADE_MIN_RELEVANCE]
        if not relevances:
            return (0.0, 0)
        return (max(relevances), len(relevances))
    except Exception as e:
        logging.error(f"_verify_followup failed for {candidate!r}: {e}")
        return (0.0, 0)

def generate_followups(query: str, results: List[Dict[str, Any]], history=None) -> List[str]:
    """Generate verified follow-up questions for the answer just returned.

    Two stages:
      (A) One gpt-4o-mini JSON call proposes candidate follow-ups grounded in the
          discourses already retrieved for `query` (their titles, answering quotes,
          and relevance scores) plus recent `history`. The prompt asks for questions
          that advance the user's goal and funnel toward higher relevance: narrower
          questions targeting strong subject matter when the current results are weak,
          deeper/adjacent questions when they are strong.
      (B) Each candidate is verified against the real retrieve->rerank->grade pipeline
          (concurrently). Only candidates that surface a directly-answering quote
          survive; survivors are ranked by the top relevance they reach and the best
          FOLLOWUP_KEEP are returned.

    Returns a list of question strings (possibly empty). Never raises — any failure
    returns [] so the UI degrades to showing no follow-ups.
    """
    if not query or not isinstance(query, str):
        return []
    if not results:
        # Nothing was retrieved for the answer -> nothing to ground or funnel from.
        return []
    history = history or []
    try:
        # --- Stage A: propose candidates, grounded in what was actually retrieved. ---
        hist_block = ""
        recent = "\n".join(
            f"- {h}" for h in history[-3:] if isinstance(h, str) and h.strip()
        )
        if recent:
            hist_block = "Earlier questions in this conversation (context only):\n" + recent + "\n\n"

        ground_lines = []
        for r in results[:8]:
            title = (r.get("title") or "Untitled").strip()
            quote = (r.get("best_sentence") or r.get("content") or "").strip()
            if len(quote) > 300:
                quote = quote[:300].rstrip() + "…"
            score = r.get("score")
            score_str = f"{float(score):.2f}" if isinstance(score, (int, float)) else "n/a"
            ground_lines.append(f'- "{title}" (relevance {score_str}): {quote}')
        ground_block = "Discourses retrieved for the current question:\n" + "\n".join(ground_lines)

        system = (
            "You generate follow-up questions for a search tool over English-translated "
            "spiritual discourses by Sathya Sai Baba. The user just asked a question and was "
            "shown the discourses below, each with the quote that answered it and a relevance "
            f"score from 0 to 1. Propose {FOLLOWUP_CANDIDATES} STANDALONE follow-up questions as "
            'JSON: {"candidates":["..."]}. Rules: '
            "(1) Each question must advance the user's apparent goal and read naturally on its own "
            "(resolve any references to earlier turns). "
            "(2) Phrase each so the corpus can directly answer it — concrete spiritual concepts, "
            "practices, or teachings, not vague or meta questions. "
            "(3) Funnel toward relevance: if the shown relevance scores are low (the results are "
            "only tangentially related), propose NARROWER questions that target the specific "
            "subject matter the strongest quotes hint at, to reach discourses that answer more "
            "directly. If the scores are already high, propose questions that go DEEPER or explore "
            "closely adjacent teachings. "
            "(4) Make the questions distinct from each other and from the original question. "
            "JSON only, no prose."
        )
        user_content = (
            hist_block
            + "Original question: " + query + "\n\n"
            + ground_block
        )
        response = openai_client.chat.completions.create(
            model=GRADE_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw).strip()
        data = json.loads(raw)
        candidates = [
            c.strip() for c in data.get("candidates", [])
            if isinstance(c, str) and c.strip()
        ][:FOLLOWUP_CANDIDATES]
        if not candidates:
            return []

        # --- Stage B: verify each candidate actually surfaces answering quotes. ---
        verified = []  # (top_relevance, candidate)
        with ThreadPoolExecutor(max_workers=FOLLOWUP_MAX_WORKERS) as executor:
            future_to_candidate = {
                executor.submit(_verify_followup, c): c for c in candidates
            }
            for future in as_completed(future_to_candidate):
                candidate = future_to_candidate[future]
                top_relevance, hits = future.result()
                if hits >= FOLLOWUP_MIN_HITS:
                    verified.append((top_relevance, candidate))

        # Funnel: surface the follow-ups that reach the highest-relevance discourses.
        verified.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in verified[:FOLLOWUP_KEEP]]
    except Exception as e:
        logging.error(f"generate_followups failed: {e}")
        return []
