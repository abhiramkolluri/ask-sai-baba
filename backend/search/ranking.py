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
    COHERE_TIMEOUT,
    RERANK_MODEL,
    RERANK_KEEP,
    GRADE_MODEL,
    JUDGE_MODEL,
    JUDGE_TEMPERATURE,
    GRADE_MIN_RELEVANCE,
    BEST_CHUNK_SENTENCES,
)

# One shared Cohere client with a bounded timeout, built once at import (guarded
# by the key so the no-key path is unchanged). Per-call clients previously had no
# timeout, which produced a 97s rerank hang under real traffic. None when the key
# is absent -> callers fall back to hybrid-score order, exactly as before.
_cohere_client = None
if COHERE_API_KEY:
    try:
        import cohere
        _cohere_client = cohere.Client(COHERE_API_KEY, timeout=COHERE_TIMEOUT)
    except Exception as e:  # pragma: no cover - import/init guard
        logging.error(f"Cohere client init failed: {e}; reranking disabled.")
        _cohere_client = None


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

    if _cohere_client is None:
        logging.warning(
            "Cohere reranking unavailable (no COHERE_API_KEY); sorting by hybrid "
            "score. Set COHERE_API_KEY in the EB environment to enable reranking."
        )
        ranked = sorted(candidates, key=lambda c: c.get("score", 0.0), reverse=True)
        return ranked[:keep]

    try:
        documents = [c.get("content", "") for c in candidates]
        response = _cohere_client.rerank(
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
        ranked = sorted(candidates, key=lambda c: c.get("score", 0.0), reverse=True)
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
    if _cohere_client is not None and flat:
        try:
            documents = [w for (_, w) in flat]
            response = _cohere_client.rerank(
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

def grade_and_quote_passages(query: str, passages: List[Dict[str, Any]], trace_out=None) -> List[Dict[str, Any]]:
    """Unified extractive grade: ONE stronger-LLM call judges whether each passage
    directly answers `query` AND extracts the verbatim span that answers it.

    Keeps only passages where `answers` is true, `relevance >= GRADE_MIN_RELEVANCE`,
    and a non-null quote can be located verbatim in the passage — attaching the exact
    substring as `best_sentence` and `grade_relevance`. Because the quote is the
    evidence for the verdict, relevance and the shown quote can no longer disagree.

    On any LLM/parse failure, degrades to the Cohere chunk baseline
    (`select_best_sentences`) and keeps everything (nothing dropped).

    ``trace_out`` (optional dict): when provided, records the grader verdicts for
    the transparency trace — model, kept/rejected counts, per-kept-passage
    (title/facet/relevance), per-rejected-passage (title/facet/reason), and a
    `fallback` flag set when the Cohere baseline was used (no verdicts available).
    Passage bodies are never recorded.
    """
    if not passages:
        return []
    if trace_out is not None:
        trace_out["model"] = JUDGE_MODEL

    system_prompt = (
        "The passages below are excerpts from spiritual discourses (lectures) delivered by "
        "Sathya Sai Baba, a revered Indian spiritual teacher. His teachings often illustrate "
        "points with stories, scripture, and Sanskrit terms.\n\n"
        "Each passage is paired with ITS OWN question. Judge whether each passage DIRECTLY "
        "answers ITS question, and if so extract the exact quote that answers it. A passage "
        "that merely mentions the topic, or is broadly on-theme but does not address its "
        "question, does NOT answer it.\n\n"
        # Rule added after auditing real questions: for HOW/WHICH questions the grader was
        # accepting quotes that only NAME a virtue ("Such was his control over his senses!")
        # instead of ones that teach or decide. Demand substance for these.
        "If the question asks HOW to do something, or WHICH of several options is better, the "
        "quote must actually INSTRUCT (give a method, step, or practice) or ADJUDICATE (say "
        "which one and why). A quote that only names, praises, or asserts the virtue/faculty "
        "exists — or merely lists items without choosing — does NOT answer such a question.\n\n"
        # Rule added after auditing real questions: homonym/metaphor traps (physical
        # 'exercise' vs spiritual exercise/sadhana; pet 'cats' vs 'cats' as a metaphor for
        # restlessness; a named text vs a same-named concept) were being accepted.
        "WORD SENSE: if the passage uses a key word from the question in a DIFFERENT SENSE "
        "than the question intends, it does NOT answer it (mark answers:false) — do not be "
        "fooled by a shared word used to mean something else.\n\n"
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
            temperature=JUDGE_TEMPERATURE,
        )
        raw = (response.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw).strip()
        data = json.loads(raw)

        def _reject(idx, reason):
            if trace_out is not None:
                p = passages[idx]
                trace_out.setdefault("rejected_passages", []).append({
                    "title": p.get("title", ""),
                    "facet": p.get("source_query") or query,
                    "reason": reason,
                })

        kept = []
        for item in data.get("results", []):
            idx = item.get("id")
            if not isinstance(idx, int) or idx < 0 or idx >= len(passages):
                continue
            if item.get("answers") is not True:
                _reject(idx, "not_answering")
                continue
            relevance = float(item.get("relevance", 0.0) or 0.0)
            if relevance < GRADE_MIN_RELEVANCE:
                _reject(idx, "low_relevance")
                continue
            exact = _locate_verbatim(item.get("quote"), passages[idx].get("content", ""))
            if not exact:
                _reject(idx, "quote_not_verbatim")  # avoid hallucinated quotes
                continue
            p = dict(passages[idx])
            p["grade_relevance"] = relevance
            p["best_sentence"] = exact
            kept.append(p)
            if trace_out is not None:
                trace_out.setdefault("kept_passages", []).append({
                    "title": p.get("title", ""),
                    "facet": p.get("source_query") or query,
                    "relevance": relevance,
                })
        if trace_out is not None:
            trace_out["kept"] = len(kept)
            trace_out["rejected"] = len(trace_out.get("rejected_passages", []))
        return kept
    except Exception as e:
        logging.error(f"grade_and_quote_passages failed: {e}; falling back to Cohere chunks (no drop).")
        if trace_out is not None:
            trace_out["fallback"] = True
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
        })

    discourses.sort(key=lambda d: d["score"], reverse=True)
    return discourses[:limit]
