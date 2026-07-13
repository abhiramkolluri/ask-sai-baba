"""Stage 3 of the pipeline — turn retrieved candidates into vetted citations.

Given the passages ``retrieval.py`` fetched, this module:

  1. **Reranks** them with Cohere (``rerank_passages``), degrading gracefully to
     hybrid-score order when ``COHERE_API_KEY`` is unset.
  2. **Selects the answering quote** — either the LLM extractive grader
     (``grade_and_quote_passages``, the live path) or the Cohere best-chunk
     baseline (``select_best_sentences``, the fallback).
  3. **Aggregates** the survivors up to one result per discourse
     (``aggregate_to_discourses``).

The two grading functions are deliberately separate: ``grade_and_quote_passages``
is the unified judge+extract used today; ``grade_passages`` is the earlier
relevance-only grader, kept available.

``cohere`` is imported lazily inside the functions so the package imports cleanly
even where the optional dependency / key is absent.
"""

import re
import json
import logging
from typing import List, Dict, Any

from .config import (
    openai_client,
    COHERE_API_KEY,
    RERANK_MODEL,
    RERANK_KEEP,
    GRADE_MODEL,
    JUDGE_MODEL,
    GRADE_MIN_RELEVANCE,
    BEST_CHUNK_SENTENCES,
    LLM_TEMPERATURE,
    LLM_SEED,
)


# ===========================================================================
# Reranking (Cohere, with graceful no-key fallback to hybrid-score order)
# ===========================================================================

def rerank_passages(query: str, candidates: List[Dict[str, Any]], keep: int = RERANK_KEEP) -> List[Dict[str, Any]]:
    """Rerank passage candidates with Cohere, or fall back to hybrid-score order.

    Works without Cohere: if COHERE_API_KEY is unset (or the call fails), the
    candidates are simply sorted by their hybrid score and truncated.
    """
    if not candidates:
        return []

    if not COHERE_API_KEY:
        logging.warning(
            "COHERE_API_KEY not set; skipping Cohere rerank and sorting by hybrid "
            "score. Set COHERE_API_KEY in the EB environment to enable reranking."
        )
        ranked = sorted(candidates, key=lambda c: (-c.get("score", 0.0), c.get("_id", "")))
        return ranked[:keep]

    try:
        import cohere
        co = cohere.Client(COHERE_API_KEY)
        documents = [c.get("content", "") for c in candidates]
        response = co.rerank(
            model=RERANK_MODEL,
            query=query,
            documents=documents,
            top_n=min(keep, len(documents)),
        )
        reranked = []
        for result in response.results:
            candidate = dict(candidates[result.index])
            candidate["rerank_score"] = result.relevance_score
            reranked.append(candidate)
        return reranked
    except Exception as e:
        logging.error(f"Cohere rerank failed: {e}; falling back to hybrid-score order.")
        ranked = sorted(candidates, key=lambda c: (-c.get("score", 0.0), c.get("_id", "")))
        return ranked[:keep]


# ===========================================================================
# Best-chunk quote selection (Cohere over sentence windows) — grader fallback
# ===========================================================================

def _split_sentences(text: str) -> List[str]:
    """Split text into sentences with a lightweight regex (no nltk dependency).

    Drops empty/trivial fragments. Abbreviations (e.g. "Mr.") can over-split, but
    that is acceptable: the rerank below still picks the most relevant fragment.
    """
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p and len(p.strip()) > 1]

def _sentence_windows(sentences: List[str], size: int = BEST_CHUNK_SENTENCES) -> List[str]:
    """Contiguous windows of up to `size` sentences (sliding, step 1), each joined
    into one string. Passages with `size` or fewer sentences yield a single
    whole-passage window.
    """
    n = len(sentences)
    if n == 0:
        return []
    if n <= size:
        return [" ".join(sentences)]
    return [" ".join(sentences[i:i + size]) for i in range(0, n - size + 1)]

def select_best_sentences(query: str, discourses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Annotate each discourse with `best_sentence`: the contiguous 2–3 sentence
    chunk from its matched passage that best answers `query`.

    Uses ONE Cohere rerank call over the candidate chunks of all passages combined,
    then assigns each discourse its highest-ranked chunk. Degrades to the passage's
    leading chunk when COHERE_API_KEY is unset or the call fails — never throws.
    """
    if not discourses:
        return discourses

    per_doc_windows = []
    flat = []  # (discourse_index, chunk)
    for d_idx, d in enumerate(discourses):
        sentences = _split_sentences(d.get("matched_passage", "") or d.get("content", ""))
        windows = _sentence_windows(sentences)
        per_doc_windows.append(windows)
        for w in windows:
            flat.append((d_idx, w))

    def _default(d_idx):
        windows = per_doc_windows[d_idx]
        if windows:
            return windows[0]
        d = discourses[d_idx]
        return d.get("matched_passage", "") or d.get("content", "")

    chosen = {}  # discourse_index -> best chunk
    if COHERE_API_KEY and flat:
        try:
            import cohere
            co = cohere.Client(COHERE_API_KEY)
            documents = [w for (_, w) in flat]
            response = co.rerank(
                model=RERANK_MODEL,
                query=query,
                documents=documents,
                top_n=len(documents),
            )
            # Results are best-first; the first chunk seen per discourse wins.
            for result in response.results:
                d_idx, chunk = flat[result.index]
                if d_idx not in chosen:
                    chosen[d_idx] = chunk
        except Exception as e:
            logging.error(f"Cohere best-chunk selection failed: {e}; using leading chunk.")
            chosen = {}

    for d_idx, d in enumerate(discourses):
        d["best_sentence"] = chosen.get(d_idx) or _default(d_idx)
    return discourses


# ===========================================================================
# LLM grading — judge relevance and extract a verbatim answering quote
# ===========================================================================

def _locate_verbatim(quote: str, passage: str):
    """Return the exact substring of `passage` matching `quote`, tolerant of
    whitespace/newline differences, or None when the quote is not actually present.
    """
    if not quote or not passage:
        return None
    tokens = quote.split()
    if not tokens:
        return None
    pattern = r"\s+".join(re.escape(t) for t in tokens)
    m = re.search(pattern, passage)
    return m.group(0) if m else None

def grade_and_quote_passages(query: str, passages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Unified extractive grade: ONE stronger-LLM call judges whether each passage
    directly answers `query` AND extracts the verbatim span that answers it.

    Keeps only passages where `answers` is true, `relevance >= GRADE_MIN_RELEVANCE`,
    and a non-null quote can be located verbatim in the passage — attaching the exact
    substring as `best_sentence` and `grade_relevance`. Because the quote is the
    evidence for the verdict, relevance and the shown quote can no longer disagree.

    On any LLM/parse failure, degrades to the Cohere chunk baseline
    (`select_best_sentences`) and keeps everything (nothing dropped).
    """
    if not passages:
        return []

    system_prompt = (
        "The passages below are excerpts from spiritual discourses (lectures) delivered by "
        "Sathya Sai Baba, a revered Indian spiritual teacher. His teachings often illustrate "
        "points with stories, scripture, and Sanskrit terms.\n\n"
        "Each passage is paired with ITS OWN question. Judge whether each passage DIRECTLY "
        "answers ITS question, and if so extract the exact quote that answers it. A passage "
        "that merely mentions the topic, or is broadly on-theme but does not address its "
        "question, does NOT answer it. EXCEPTION: when the question asks about a specific "
        "story, parable, person, or incident, a passage that NARRATES that story or incident "
        "DOES directly answer it — quote the span most relevant to what was asked. "
        "For each passage return its id, `answers` (true/false), "
        "`relevance` (0.0-1.0), and `quote`: the shortest contiguous span of 1 to 3 sentences "
        "copied EXACTLY (verbatim) from the passage that answers its question, or null if it "
        "does not answer it. Never quote generic, introductory, or closing remarks (e.g. 'I "
        "shall bring my discourse to a close'). Respond ONLY with JSON: "
        '{"results":[{"id":0,"answers":true,"relevance":0.0,"quote":"..."}]}. No prose.'
    )
    lines = ["Passages (each with its own question):", ""]
    for idx, p in enumerate(passages):
        facet = p.get("source_query") or query
        lines.append(f"[{idx}] Question: {facet}")
        lines.append(f"Passage: {p.get('content', '')}")
        lines.append("")
    user_content = "\n".join(lines)

    try:
        response = openai_client.chat.completions.create(
            model=JUDGE_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
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

        kept = []
        for item in data.get("results", []):
            idx = item.get("id")
            if not isinstance(idx, int) or idx < 0 or idx >= len(passages):
                continue
            if item.get("answers") is not True:
                continue
            relevance = float(item.get("relevance", 0.0) or 0.0)
            if relevance < GRADE_MIN_RELEVANCE:
                continue
            exact = _locate_verbatim(item.get("quote"), passages[idx].get("content", ""))
            if not exact:
                continue  # no verbatim answering span -> drop (avoid hallucinated quotes)
            p = dict(passages[idx])
            p["grade_relevance"] = relevance
            p["best_sentence"] = exact
            kept.append(p)
        return kept
    except Exception as e:
        logging.error(f"grade_and_quote_passages failed: {e}; falling back to Cohere chunks (no drop).")
        return select_best_sentences(query, passages)

def grade_passages(query: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """LLM relevance grade: keep only passages that directly answer the query.

    A single batched gpt-4o-mini call in JSON mode. On any parse/API failure the
    reranked candidates are returned unfiltered rather than crashing.
    """
    if not candidates:
        return []

    system_prompt = (
        "You judge whether each passage from a collection of spiritual discourses directly "
        "answers the user's question. A passage that merely mentions the topic, or is broadly "
        "on-theme but does not address what was asked, does NOT answer it. For each passage "
        "return its id, `answers` (true/false), and `relevance` (0.0-1.0). Respond ONLY with "
        'JSON: {"results":[{"id":0,"answers":true,"relevance":0.0}]}. No prose, no markdown.'
    )

    lines = [f"Question: {query}", "", "Passages:"]
    for idx, candidate in enumerate(candidates):
        lines.append(f"{idx}: {candidate.get('content', '')}")
    user_content = "\n".join(lines)

    try:
        response = openai_client.chat.completions.create(
            model=GRADE_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=LLM_TEMPERATURE,
            seed=LLM_SEED,
        )
        raw = (response.choices[0].message.content or "").strip()
        # Strip code fences defensively before parsing.
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw).strip()
        data = json.loads(raw)

        graded = []
        for item in data.get("results", []):
            idx = item.get("id")
            if not isinstance(idx, int) or idx < 0 or idx >= len(candidates):
                continue
            relevance = float(item.get("relevance", 0.0) or 0.0)
            if item.get("answers") is True and relevance >= GRADE_MIN_RELEVANCE:
                candidate = dict(candidates[idx])
                candidate["grade_relevance"] = relevance
                graded.append(candidate)
        return graded
    except Exception as e:
        logging.error(f"grade_passages failed: {e}; returning reranked candidates unfiltered.")
        return candidates


# ===========================================================================
# Aggregation — collapse graded passages to one citation per discourse
# ===========================================================================

def aggregate_to_discourses(graded: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Collapse graded passages to one result per discourse (best passage wins)."""
    best_by_article = {}
    for passage in graded:
        article_id = passage.get("article_id", "")
        relevance = passage.get("grade_relevance", passage.get("score", 0.0))
        existing = best_by_article.get(article_id)
        if existing is None or relevance > existing["_relevance"]:
            best_by_article[article_id] = {"_relevance": relevance, "passage": passage}

    discourses = []
    for article_id, entry in best_by_article.items():
        passage = entry["passage"]
        relevance = entry["_relevance"]
        matched_passage = passage.get("content", "")
        discourses.append({
            "_id": article_id,
            "title": passage.get("title", ""),
            "matched_passage": matched_passage,
            "passage_index": passage.get("chunk_index", 0),
            "content": matched_passage,  # keep `content` = passage for frontend/format_docs
            "best_sentence": passage.get("best_sentence", ""),  # verified answering quote
            "score": relevance,
            "location": passage.get("location", ""),
            "occasion": passage.get("occasion", ""),
            "link": passage.get("link", ""),
            "collection": passage.get("collection_name", ""),  # output key is `collection`
            "date_authored": passage.get("date_authored", ""),
            "date": passage.get("date_authored", ""),  # frontend reads `date`
        })

    discourses.sort(key=lambda d: (-d["score"], d["_id"]))
    return discourses[:limit]
