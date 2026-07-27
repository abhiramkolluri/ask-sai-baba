"""Top-level orchestration — wire the pipeline stages into public entrypoints.

This module is the conductor: it calls query planning, then per-facet retrieval +
reranking, fuses the lists, runs the extractive grader, and aggregates to
citations. The heavy lifting lives in the stage modules
(``query_planning`` / ``retrieval`` / ``ranking``); here we only sequence them.

Public entrypoints:
  - ``search_browse``      — the main discourse search used by ``/search`` and chat.
  - ``grade_passages_by_id`` — phase 2 of the two-phase response (``/search/verify``):
    grades the candidates a ``defer_grading=True`` search returned.
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
    RERANK_PROVIDER,
    MERGE_MIN_PER_FACET,
    MERGE_PER_FACET_CAP,
    FACET_MAX_WORKERS,
    ROUTER_V2_ENABLED,
    CHAT_FALLBACK_DISCOURSES,
    FOLLOWUP_CANDIDATES,
    FOLLOWUP_KEEP,
    FOLLOWUP_OVERFETCH,
    FOLLOWUP_RERANK_KEEP,
    FOLLOWUP_MIN_HITS,
    FOLLOWUP_MIN_RERANK_SCORE,
    FOLLOWUP_MAX_WORKERS,
    LLM_TEMPERATURE,
    LLM_SEED,
)
from .query_planning import plan_queries
from .retrieval import (search_passages, search_exact, _rrf_merge, phrase_in_text,
                        fetch_passages_by_ids, build_passage_filter)
from .ranking import (
    rerank_passages,
    grade_and_quote_passages,
    aggregate_to_discourses,
    select_best_sentences,
)
from .transparency import new_trace, StageTimer, assess_quality
from .resilience import PipelineServiceError
from .knowledge import lookup_entity
from .listing import list_discourses
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

def search_browse(query: str, limit: int = 5, exact_phrase: str = None, allow_empty: bool = True, history=None, return_trace: bool = False, defer_grading: bool = False):
    """Search discourses via the passage pipeline (hybrid -> rerank -> grade -> aggregate).

    Keeps the quoted-phrase exact-match shortcut from the legacy implementation.
    `allow_empty` controls the no-results behavior:
      - True (browse): return [] when nothing directly answers the query.
      - False (chat): fall back to the top reranked passages so the generator
        still has grounding context.

    `defer_grading` (opt-in): when True this is phase 1 of the two-phase response —
    candidates are retrieved and reranked but NOT graded, so the call returns in
    roughly a quarter of the usual time. Results are provisional: no quotes, and
    `trace.deferred` is set. The caller is expected to post
    `trace.pending_passage_ids` to ``grade_passages_by_id`` for verified quotes.

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
    cache_args = (query, history, exact_phrase, allow_empty, return_trace, defer_grading)
    cached = cache.get(*cache_args)
    if cached is not None:
        # Tag the trace so the stage timings below aren't misread as this
        # request's work — they belong to the original run that populated the
        # entry. Copied rather than mutated in place so concurrent requests
        # never share a half-written dict.
        if return_trace and isinstance(cached, tuple) and len(cached) == 2:
            results, trace = cached
            return results, {**trace, "cached": True}
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
            cacheable = True
        else:
            trace["results"]["discourses"] = len(results)
            trace["results"]["top_relevance"] = results[0].get("score", 0.0) if results else 0.0
            assess_quality(trace, results)
            value = (results, trace)
            # NEVER cache a failure, and treat "empty" as a failure unless the
            # emptiness was a DELIBERATE routing decision.
            #
            # An earlier version excluded only service errors and failed
            # listings. That left a hole: the router is not deterministic, and a
            # flake can degrade a perfectly good question into a semantic search
            # that finds nothing — which is neither of those cases, so it got
            # cached and replayed forever. Measured on "return the first 5
            # chapters from Prema Vahini": 6/6 correct with the cache off, but a
            # single flaked run poisoned the entry and every later request
            # returned nothing.
            #
            # So: cache anything that produced results, plus the abstentions we
            # arrived at on purpose (out-of-domain, meta, a known corpus gap,
            # a collection we genuinely could not find). Everything else is
            # cheap to recompute and might have been a bad roll.
            deliberate_abstention = (
                trace.get("route") == "guidance"
                or trace.get("kb_gap")
                or (trace.get("listing") or {}).get("not_found")
            )
            cacheable = (
                not trace.get("service_error")
                and trace.get("quality") != "error"
                and (bool(results) or deliberate_abstention)
            )
        if cacheable:
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
        # "unanswerable" is checked HERE, before the listing dispatch below, and the
        # order is the whole fix. "What is the most important discourse in Prema
        # Vahini?" names a collection, so the listing route used to claim it and
        # return chapter 1 as a confident answer to a ranking nobody ever wrote
        # down. A judgment about a collection is not an enumeration of it.
        if ROUTER_V2_ENABLED and intent in ("meta", "out_of_domain", "unanswerable"):
            if trace:
                trace["route"] = "guidance"
                # The router's own sentence about THIS question. None when it
                # didn't produce a usable one; the frontend then falls back to
                # static copy rather than showing nothing.
                trace["unanswerable_reason"] = plan_meta.get("unanswerable_reason")
            return _finish([])

        # (c.1b) Listing route: "return the first 5 chapters from Prema Vahini" —
        # enumerate a named collection's chapters IN READING ORDER (filter+sort on
        # the backfilled chapter_index), not a relevance search. Abstains honestly
        # when the collection isn't found.
        # A "listing" intent with no collection name is an INCOMPLETE plan, not a
        # missing collection. PLAN_TEMPERATURE=0.0 does not actually make
        # gpt-4o-mini deterministic — measured ~1-in-3 on "return the first 5
        # chapters from Prema Vahini", where the same question yielded
        # collection=None on one run and "Prema Vahini" on the next. Routing the
        # incomplete plan to the listing route made us tell the user we couldn't
        # find a collection they had named correctly. Falling through to semantic
        # search returns something useful instead of a confident falsehood.
        if (ROUTER_V2_ENABLED and intent == "listing"
                and not plan_meta.get("filters") and not plan_meta.get("book_requested")):
            logging.warning(
                f"listing intent with no filters for {query!r}; "
                "falling through to semantic route (incomplete plan)."
            )
            intent = "conceptual"
            if trace:
                trace["intent"] = intent

        if ROUTER_V2_ENABLED and intent == "listing":
            filters = plan_meta.get("filters") or {}
            # A book the catalog did not recognise still reaches the listing
            # route, where _resolve_book_name gets a chance at the spelling. If
            # that fails too, "we don't have that book" is the honest answer —
            # far better than semanticizing it into plausible-looking results.
            if not filters.get("book") and plan_meta.get("book_requested"):
                filters = {**filters, "book": plan_meta["book_requested"]}
            with StageTimer(trace, "listing"):
                lst = list_discourses(filters,
                                      sort=plan_meta.get("sort"),
                                      limit=plan_meta.get("limit"),
                                      order=plan_meta.get("list_order"))
            if trace:
                trace["route"] = "listing"
                trace["listing"] = {
                    "collection": lst.get("book") or filters.get("book"),
                    "filters": filters,
                    "count": len(lst.get("results", [])),
                    "order": plan_meta.get("list_order") or "first",
                    "not_found": lst["status"] != "found",
                }
            # location/occasion values are sparse and messy in the corpus, so an
            # empty listing on those is more likely a metadata gap than a true
            # "we don't have any" — fall through to a semantic search rather
            # than asserting absence. Book/chapter/year are trustworthy, so an
            # empty result there is the honest answer.
            sparse = "location" in filters or "occasion" in filters
            if lst["status"] == "found" or not sparse:
                return _finish(lst.get("results", []))
            logging.info(f"listing on sparse metadata {filters} empty; trying semantic route")
            intent = "conceptual"
            if trace:
                trace["intent"] = intent

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

        # Hybrid: a topic question that also named metadata ("what does the
        # Geeta Vahini say about karma", "teachings on devotion from the 1990s")
        # keeps its intent and narrows the candidate set. Composed once here
        # rather than per facet — every facet of one message shares it.
        passage_filter = build_passage_filter(plan_meta.get("filters"))

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
                cands = search_passages(q, PASSAGE_OVERFETCH, occasion=occ,
                                        filters=passage_filter)
            except PipelineServiceError as e:
                logging.error(f"facet retrieval errored for {q!r}: {e}")
                return [], {"facet": q, "retrieved": 0, "kept_after_rerank": 0,
                            "reranker": "n/a", "rerank_degraded": False,
                            "ms": int((time.perf_counter() - started) * 1000),
                            "errored": True}
            rl = rerank_passages(q, cands, RERANK_KEEP)
            for p in rl:
                p["source_query"] = q
            # Whether reranking actually ran. rerank_passages stamps every passage
            # with `rerank_degraded` when it fell back to hybrid-score order, so
            # this is read from an explicit flag rather than inferred from the
            # presence of a score field.
            degraded = bool(rl) and rl[0].get("rerank_degraded", False)
            reranker = "n/a" if not rl else ("hybrid_score" if degraded else RERANK_PROVIDER)
            stat = {
                "facet": q,
                "retrieved": len(cands),
                "kept_after_rerank": len(rl),
                "reranker": reranker,
                "rerank_degraded": degraded,
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
        filter_fell_back = False
        if occasion and sum(s["retrieved"] for s in facet_stats) < 5:
            # The occasion filter found (almost) nothing — the occasion name may
            # not match corpus metadata. Retry unfiltered rather than returning a
            # hollow result for a real topic; the trace records the fallback so
            # the UI can say so honestly.
            fell_back = True
            ranked_lists, facet_stats = _retrieve_and_rerank(None)

        # Same treatment for the router's metadata filters. location/occasion in
        # particular are sparse and inconsistently spelled in the corpus, so a
        # filter matching nothing is far more likely a metadata gap than a real
        # absence — "Aradhana day" returned zero because no passage carries that
        # occasion, not because the discourses are silent on it.
        if passage_filter is not None and sum(s["retrieved"] for s in facet_stats) < 5:
            logging.info(f"metadata filter {plan_meta.get('filters')} matched almost nothing; retrying unfiltered")
            passage_filter = None
            filter_fell_back = True
            ranked_lists, facet_stats = _retrieve_and_rerank(occasion if not fell_back else None)

        # Record per-facet stats first so the trace is complete even when we
        # short-circuit on a service error below.
        if trace:
            trace["retrieval"]["facet_results"] = facet_stats
            # One degraded facet is enough to caveat the ordering of the whole
            # merged list, since RRF fuses them together.
            if any(s.get("rerank_degraded") for s in facet_stats):
                trace["rerank_degraded"] = True
            if occasion:
                trace["metadata_filter"] = {
                    "occasion": occasion,
                    "applied": not fell_back,
                    "fell_back": fell_back,
                }
            if plan_meta.get("filters"):
                trace["filters"] = {
                    "requested": plan_meta["filters"],
                    "applied": not filter_fell_back,
                    "fell_back": filter_fell_back,
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

        # (c.5) Deferred grading — phase 1 of the two-phase response. Grading is
        # ~74% of this pipeline's latency, so returning the reranked candidates
        # now lets the UI paint in about a quarter of the time; the caller then
        # posts the passage ids to /search/verify to get verified quotes.
        #
        # Everything returned here is PROVISIONAL: no passage has been judged, so
        # `best_sentence` is deliberately left empty rather than filled with an
        # unverified guess, and results are ordered by reranker score. The caller
        # must not present these as answers — the trace is marked `deferred` so a
        # client that ignores phase 2 cannot silently show unverified quotes.
        if defer_grading:
            results = aggregate_to_discourses(reranked, limit)
            if trace:
                trace["deferred"] = True
                trace["pending_passage_ids"] = [
                    r["passage_id"] for r in results if r.get("passage_id")
                ]
            return _finish(results)

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

def grade_passages_by_id(query: str, passage_specs, limit: int = 10, return_trace: bool = False):
    """Phase 2 of the two-phase response: grade the candidates phase 1 returned.

    `passage_specs` is a list of ``{"passage_id": str, "facet": str}`` — the ids
    come from phase 1's ``trace.pending_passage_ids`` (or each citation's
    ``passage_id``), and `facet` is the sub-query that surfaced it, so each
    passage is still judged against the question it was actually retrieved for
    rather than the raw user message.

    Stateless by design: the passages are re-read from Weaviate rather than held
    in memory between the two calls, so this works across EB instances and
    restarts. Returns the same ``(results, trace)`` shape as ``search_browse`` so
    the client can swap phase 1's provisional list for this one wholesale.

    Passages the grader rejects are simply absent from the result — that is what
    makes a provisional card disappear, and it is the honest outcome.
    """
    trace = new_trace(query, None) if return_trace else None
    grade_meta = trace["grading"] if trace else None

    def _finish(results):
        if trace is None:
            return results
        trace["results"]["discourses"] = len(results)
        trace["results"]["top_relevance"] = results[0].get("score", 0.0) if results else 0.0
        trace["route"] = "semantic"
        assess_quality(trace, results)
        return results, trace

    ids, facet_by_id = [], {}
    for spec in passage_specs or []:
        if not isinstance(spec, dict):
            continue
        pid = (spec.get("passage_id") or "").strip()
        if pid:
            ids.append(pid)
            facet_by_id[pid] = (spec.get("facet") or query).strip() or query
    if not ids:
        return _finish([])

    with StageTimer(trace, "total"):
        with StageTimer(trace, "retrieval"):
            by_id = fetch_passages_by_ids(ids)
        # Preserve phase 1's ordering, and drop ids that no longer resolve (a
        # re-index between the two calls) rather than failing the whole request.
        passages = []
        for pid in ids:
            p = by_id.get(pid)
            if p is not None:
                p["source_query"] = facet_by_id[pid]
                passages.append(p)
        if not passages:
            logging.warning(
                f"/search/verify: none of {len(ids)} passage ids resolved — "
                "corpus may have been re-indexed since phase 1."
            )
            return _finish([])

        with StageTimer(trace, "grading"):
            graded = grade_and_quote_passages(query, passages, trace_out=grade_meta)
        return _finish(aggregate_to_discourses(graded, limit))


def search(user_query: str, collection=None) -> List[Dict[str, Any]]:
    """Search for documents."""
    return search_browse(user_query)


# ===========================================================================
# Follow-up question generation — propose, then verify against the real pipeline
# ===========================================================================

def _verify_followup(candidate: str):
    """Run a candidate follow-up through a lightweight version of the search
    pipeline and report whether the corpus can answer it.

    The candidate is already a standalone question, so plan_queries is skipped and
    it is treated as its own single facet. Returns (top_score, hit_count): the
    highest reranker relevance among candidate passages and how many cleared
    FOLLOWUP_MIN_RERANK_SCORE. (0.0, 0) means nothing answers it -> dropped.

    Verification is reranker-only. It previously ran the full LLM grader on every
    candidate, which meant 6 judge calls per user question — the single largest
    line item in per-question cost — purely to decide which suggestion chips to
    show. The reranker's score answers the same question ("can this be answered
    from the corpus?") at ~1/100th the cost.
    """
    try:
        cands = search_passages(candidate, FOLLOWUP_OVERFETCH)
        reranked = rerank_passages(candidate, cands, FOLLOWUP_RERANK_KEEP)
        if not reranked:
            return (0.0, 0)
        # A degraded rerank leaves only hybrid scores, which are on a different
        # (uncalibrated) scale — thresholding them would silently admit or reject
        # candidates on noise. Treat as unverifiable and drop, loudly.
        if reranked[0].get("rerank_degraded"):
            logging.warning(
                f"follow-up verification skipped for {candidate!r}: reranking degraded, "
                "no calibrated score to threshold on."
            )
            return (0.0, 0)
        scores = [p.get("rerank_score", 0.0) for p in reranked]
        scores = [s for s in scores if s >= FOLLOWUP_MIN_RERANK_SCORE]
        if not scores:
            return (0.0, 0)
        return (max(scores), len(scores))
    except Exception as e:
        logging.error(f"_verify_followup failed for {candidate!r}: {e}")
        return (0.0, 0)

def generate_followups(query: str, results: List[Dict[str, Any]], history=None,
                       intent: str = None, unanswerable_reason: str = None) -> List[str]:
    """Generate verified follow-up questions for the answer just returned.

    Two stages:
      (A) One gpt-4o-mini JSON call proposes candidate follow-ups grounded in the
          discourses already retrieved for `query` (their titles, answering quotes,
          and relevance scores) plus recent `history`. The prompt asks for questions
          that advance the user's goal and funnel toward higher relevance: narrower
          questions targeting strong subject matter when the current results are weak,
          deeper/adjacent questions when they are strong.
      (B) Each candidate is verified against the real retrieve->rerank pipeline
          (concurrently). Only candidates whose top passage clears
          FOLLOWUP_MIN_RERANK_SCORE survive; survivors are ranked by the score they
          reach and the best FOLLOWUP_KEEP are returned.

    REDIRECT MODE (`intent="unanswerable"` OR no results at all): there is
    nothing to ground on because the user got no answer — either the question was
    refused (a ranking nobody made, our own opinion, a prediction about one
    person) or the search simply matched nothing. Both leave the same dead end, so
    both get redirects. Stage A rewrites THEIR question into one the corpus can
    answer — keeping the subject and what they wanted to know, changing only the
    part that cannot be answered — and offers further angles after it. Stage B is
    unchanged and is what makes this safe: suggesting a redirect that also fails
    would compound the original dead end, so every candidate is verified against
    real retrieval before it is shown.

    Returns a list of question strings (possibly empty). Never raises — any failure
    returns [] so the UI degrades to showing no follow-ups.
    """
    if not query or not isinstance(query, str):
        return []
    # Redirect whenever the user got NO ANSWER, not only on an explicit refusal.
    # A question that simply matched nothing (out_of_domain, a known KB gap, a
    # listing we don't hold, or plain no-matches) leaves exactly the same dead end
    # as a refusal: an empty screen and no idea what to ask next. The only thing
    # that differs is why, which is what `unanswerable_reason` carries.
    redirect = intent == "unanswerable" or not results
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

        if redirect:
            # No discourses were retrieved — by design. Tell the model what was
            # asked and why it could not be answered, and ask it to move sideways
            # to something the corpus does address, keeping whatever subject the
            # person actually cares about ("most important discourse in Prema
            # Vahini" -> what Prema Vahini teaches about divine love).
            system = (
                "A user asked a search tool over English-translated spiritual discourses by "
                "Sathya Sai Baba a question it CANNOT answer. Your job is to offer questions it "
                "CAN answer, staying as close as possible to what the person actually wants.\n\n"
                # Be accurate about WHY: telling the model a question "asks for a
                # judgment" when it merely matched nothing sends it rewriting the
                # wrong flaw. A refusal carries its own reason; an empty search
                # does not, and the honest fallback is that nothing matched.
                f"Why the original could not be answered: "
                f"{unanswerable_reason or ('It asks for a judgment, opinion, or prediction that no discourse states.' if intent == 'unanswerable' else 'The search found no discourse that answers it — it may be outside what these discourses cover, or phrased in a way the corpus does not match.')}\n\n"
                f'Propose {FOLLOWUP_CANDIDATES} STANDALONE questions as JSON: {{"candidates":["..."]}}. Rules: '
                "(1) THE FIRST CANDIDATE MUST BE A REWRITE OF THEIR OWN QUESTION, not a related "
                "question. Keep their subject and what they were actually trying to find out, and "
                "change ONLY the part that cannot be answered. It should read like the question they "
                "would have asked if they had known what this tool can do. Examples: "
                '"What is the most important discourse from the Prema Vahini" -> "What are the '
                'central teachings of Prema Vahini?"; "should I marry the person I am seeing" -> '
                '"What does Swami say about choosing a life partner?"; "when will I get a job" -> '
                '"What does Swami say about work and duty?"; "which discourse should I read first" '
                '-> "What does Swami say is the foundation of spiritual practice?". Do NOT drift to '
                "a merely adjacent topic — someone asking which Prema Vahini chapter is best still "
                "wants Prema Vahini, not divine love in general. "
                "(2) The remaining candidates explore the same interest from other angles. "
                "(3) Every question must be answerable from recorded discourses — concrete teachings, "
                "practices or concepts. Never ask the tool to rank, choose, recommend, or predict. "
                "(4) Make them distinct from each other. "
                "JSON only, no prose."
            )
            user_content = hist_block + "The question they asked: " + query
        else:
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
            temperature=LLM_TEMPERATURE,
            seed=LLM_SEED,
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
        verified = []  # (top_relevance, proposal_index, candidate)
        with ThreadPoolExecutor(max_workers=FOLLOWUP_MAX_WORKERS) as executor:
            future_to_candidate = {
                executor.submit(_verify_followup, c): (i, c)
                for i, c in enumerate(candidates)
            }
            for future in as_completed(future_to_candidate):
                idx, candidate = future_to_candidate[future]
                top_relevance, hits = future.result()
                if hits >= FOLLOWUP_MIN_HITS:
                    verified.append((top_relevance, idx, candidate))

        if redirect:
            # Rank by FAITHFULNESS first, then relevance. Stage A is told to make
            # candidate 0 a rewrite of the user's own question, and sorting purely
            # by score threw that away — a generic "what does Swami say about love"
            # outscores "what does Swami say about choosing a life partner" while
            # answering something the person did not ask. The rewrite still has to
            # pass the same verification as any other candidate, so keeping it
            # first cannot surface a question the corpus fails to answer.
            verified.sort(key=lambda x: (x[1] != 0, -x[0]))
        else:
            # Funnel: surface the follow-ups reaching the highest-relevance discourses.
            verified.sort(key=lambda x: x[0], reverse=True)
        return [c for _, _, c in verified[:FOLLOWUP_KEEP]]
    except Exception as e:
        logging.error(f"generate_followups failed: {e}")
        return []
