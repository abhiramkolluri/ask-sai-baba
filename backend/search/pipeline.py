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
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any

from .config import (
    openai_client,
    GRADE_MODEL,
    PASSAGE_OVERFETCH,
    RERANK_KEEP,
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
from .retrieval import search_passages, search_exact, _rrf_merge
from .ranking import (
    rerank_passages,
    grade_and_quote_passages,
    aggregate_to_discourses,
    select_best_sentences,
)


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

def search_browse(query: str, limit: int = 5, exact_phrase: str = None, allow_empty: bool = True, history=None) -> List[Dict[str, Any]]:
    """Search discourses via the passage pipeline (hybrid -> rerank -> grade -> aggregate).

    Keeps the quoted-phrase exact-match shortcut from the legacy implementation.
    `allow_empty` controls the no-results behavior:
      - True (browse): return [] when nothing directly answers the query.
      - False (chat): fall back to the top reranked passages so the generator
        still has grounding context.

    The original near_text implementation is preserved verbatim as
    search_browse_articles_legacy() for rollback.
    """
    # (a) Quoted-phrase shortcut: try exact match first, exactly as before.
    if exact_phrase:
        exact_results = search_exact(exact_phrase, limit=limit)
        if exact_results:
            # Normalize to the pipeline output shape so downstream consumers
            # (format_docs, frontend) see matched_passage/passage_index.
            for r in exact_results:
                r.setdefault("matched_passage", r.get("content", ""))
                r.setdefault("passage_index", 0)
            return select_best_sentences(query, exact_results)
        # (b) No exact match — fall through using the phrase as the query, but
        # route into the passage pipeline instead of near_text.
        query = exact_phrase

    # (c) Plan the query into 1-N standalone sub-queries (multi-turn + distillation
    #     + adaptive decomposition), retrieve and rerank EACH against its own facet,
    #     then fuse with provenance so quotes can be judged per-facet downstream.
    planned = plan_queries(query, history)
    ranked_lists = []
    for q in planned:
        cands = search_passages(q, PASSAGE_OVERFETCH)
        rl = rerank_passages(q, cands, RERANK_KEEP)
        for p in rl:
            p["source_query"] = q
        ranked_lists.append(rl)
    reranked = _rrf_merge(ranked_lists)[:RERANK_KEEP]
    # Unified extractive grade: judges relevance AND extracts the verbatim answering
    # quote per passage against its own facet, attaching `best_sentence` to each kept one.
    graded = grade_and_quote_passages(query, reranked)
    results = aggregate_to_discourses(graded, limit)

    # (d) Handle the empty case.
    if not results:
        if allow_empty:
            return []
        # Chat path: ground the generator on the top reranked (pre-grade)
        # passages even though the grader was not satisfied.
        fallback = aggregate_to_discourses(reranked[:CHAT_FALLBACK_DISCOURSES], CHAT_FALLBACK_DISCOURSES)
        return select_best_sentences(query, fallback)

    # Quotes are already attached by the unified grade step.
    return results

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
