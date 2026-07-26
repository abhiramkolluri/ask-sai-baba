"""Stage 3 of the pipeline — turn retrieved candidates into vetted citations.

Given the passages ``retrieval.py`` fetched, this module:

  1. **Reranks** them (``rerank_passages``) via the provider chosen by
     ``RERANK_PROVIDER`` — Voyage by default, Cohere as a one-line rollback —
     degrading gracefully to hybrid-score order when the key is unset or the
     call fails.
  2. **Selects the answering quote** — either the LLM extractive grader
     (``grade_and_quote_passages``, the live path) or the reranker best-chunk
     baseline (``select_best_sentences``, the fallback).
  3. **Aggregates** the survivors up to one result per discourse
     (``aggregate_to_discourses``).

The two grading functions are deliberately separate: ``grade_and_quote_passages``
is the unified judge+extract used today; ``grade_passages`` is the earlier
relevance-only grader, kept available.

The provider SDK is imported inside a guarded block so the package imports
cleanly even where the optional dependency / key is absent; all reranking goes
through the single ``_rerank`` adapter rather than calling a provider directly.
"""

import re
import json
import logging
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import (
    openai_client,
    COHERE_API_KEY,
    COHERE_TIMEOUT,
    COHERE_RERANK_MODEL,
    VOYAGE_API_KEY,
    VOYAGE_TIMEOUT,
    VOYAGE_MAX_RETRIES,
    RERANK_PROVIDER,
    RERANK_MODEL,
    RERANK_KEEP,
    GRADE_MODEL,
    JUDGE_MODEL,
    REASONING_EFFORT,
    JUDGE_IS_REASONING,
    JUDGE_TEMPERATURE,
    GRADE_MIN_RELEVANCE,
    GRADE_BATCH_SIZE,
    GRADE_MAX_WORKERS,
    BEST_CHUNK_SENTENCES,
)

# One shared rerank client with a bounded timeout, built once at import (guarded
# by the key so the no-key path is unchanged). Per-call clients previously had no
# timeout, which produced a 97s rerank hang under real traffic. None when the key
# is absent -> callers fall back to hybrid-score order, exactly as before.
_rerank_client = None
_rerank_provider = None

if RERANK_PROVIDER == "voyage" and VOYAGE_API_KEY:
    try:
        import voyageai
        _rerank_client = voyageai.Client(
            api_key=VOYAGE_API_KEY,
            timeout=VOYAGE_TIMEOUT,
            max_retries=VOYAGE_MAX_RETRIES,
        )
        _rerank_provider = "voyage"
    except Exception as e:  # pragma: no cover - import/init guard
        logging.error(f"Voyage client init failed: {e}; reranking disabled.")
        _rerank_client = None
elif RERANK_PROVIDER == "cohere" and COHERE_API_KEY:
    try:
        import cohere
        _rerank_client = cohere.Client(COHERE_API_KEY, timeout=COHERE_TIMEOUT)
        _rerank_provider = "cohere"
    except Exception as e:  # pragma: no cover - import/init guard
        logging.error(f"Cohere client init failed: {e}; reranking disabled.")
        _rerank_client = None

if _rerank_client is None:
    logging.warning(
        f"Reranking unavailable (provider={RERANK_PROVIDER!r}, key present="
        f"{bool(VOYAGE_API_KEY if RERANK_PROVIDER == 'voyage' else COHERE_API_KEY)}); "
        "results will be ordered by hybrid score."
    )


def _rerank(query, documents, top_n):
    """Rerank `documents` against `query`, provider-agnostically.

    Returns a best-first list of ``(original_index, relevance_score)`` on success,
    or ``None`` when reranking did not run (no client, or the call failed). A None
    return is the caller's signal to fall back to hybrid-score order AND to mark
    the result as degraded — silent degradation is what let a rate-limited key go
    unnoticed across 294 failures, so every failure here is logged at ERROR with
    the provider named.

    The two providers differ only in the argument name for the cutoff
    (``top_k`` vs ``top_n``); both return objects exposing ``.index`` and
    ``.relevance_score``, so the shape below is shared.
    """
    if _rerank_client is None or not documents:
        return None
    try:
        if _rerank_provider == "voyage":
            resp = _rerank_client.rerank(
                query=query,
                documents=documents,
                model=RERANK_MODEL,
                top_k=min(top_n, len(documents)),
            )
        else:
            resp = _rerank_client.rerank(
                model=COHERE_RERANK_MODEL,
                query=query,
                documents=documents,
                top_n=min(top_n, len(documents)),
            )
        return [(r.index, r.relevance_score) for r in resp.results]
    except Exception as e:
        logging.error(
            f"{_rerank_provider} rerank failed: {e}; falling back to hybrid-score order."
        )
        return None


# ===========================================================================
# Reranking (Cohere, with graceful no-key fallback to hybrid-score order)
# ===========================================================================

def rerank_passages(query: str, candidates: List[Dict[str, Any]], keep: int = RERANK_KEEP) -> List[Dict[str, Any]]:
    """Rerank passage candidates, or fall back to hybrid-score order.

    Works without a rerank provider: if the key is unset (or the call fails), the
    candidates are simply sorted by their hybrid score and truncated.

    When reranking did NOT run, every returned passage is stamped
    ``rerank_degraded = True``. That flag is what the pipeline reads to put
    "results were not reranked" on the transparency trace — a rate-limited key
    previously degraded ranking invisibly, which is the failure this makes loud.
    """
    if not candidates:
        return []

    ranked_pairs = _rerank(query, [c.get("content", "") for c in candidates], keep)

    if ranked_pairs is None:
        ranked = sorted(candidates, key=lambda c: c.get("score", 0.0), reverse=True)
        degraded = []
        for c in ranked[:keep]:
            c = dict(c)
            c["rerank_degraded"] = True
            degraded.append(c)
        return degraded

    reranked = []
    for idx, score in ranked_pairs:
        candidate = dict(candidates[idx])
        candidate["rerank_score"] = score
        reranked.append(candidate)
    return reranked


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
    if flat:
        ranked_pairs = _rerank(query, [w for (_, w) in flat], len(flat))
        if ranked_pairs is not None:
            # Results are best-first; the first chunk seen per discourse wins.
            for idx, _score in ranked_pairs:
                d_idx, chunk = flat[idx]
                if d_idx not in chosen:
                    chosen[d_idx] = chunk

    for d_idx, d in enumerate(discourses):
        d["best_sentence"] = chosen.get(d_idx) or _default(d_idx)
    return discourses


# ===========================================================================
# LLM grading — judge relevance and extract a verbatim answering quote
# ===========================================================================

_GRADE_SYSTEM_PROMPT = (
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
    "exists \u2014 or merely lists items without choosing \u2014 does NOT answer such a question.\n\n"
    # Rule added after auditing real questions: homonym/metaphor traps (physical
    # 'exercise' vs spiritual exercise/sadhana; pet 'cats' vs 'cats' as a metaphor for
    # restlessness; a named text vs a same-named concept) were being accepted.
    "WORD SENSE: if the passage uses a key word from the question in a DIFFERENT SENSE "
    "than the question intends, it does NOT answer it (mark answers:false) \u2014 do not be "
    "fooled by a shared word used to mean something else.\n\n"
    "For each passage return its id, `answers` (true/false), "
    "`relevance` (0.0-1.0), and `quote`: the shortest contiguous span of 1 to 3 sentences "
    "copied EXACTLY (verbatim) from the passage that answers its question, or null if it "
    "does not answer it. Never quote generic, introductory, or closing remarks (e.g. 'I "
    "shall bring my discourse to a close'). Respond ONLY with JSON: "
    '{"results":[{"id":0,"answers":true,"relevance":0.0,"quote":"..."}]}. No prose.'
)


def _grade_one_batch(query, passages):
    """Grade ONE batch of passages. Returns {local_index: {relevance, quote}} for
    the passages that answer their facet, or None if the call/parse failed.

    Runs on a worker thread, so it touches no shared state and never raises \u2014 a
    failed batch is reported as None and handled by the caller.
    """
    lines = ["Passages (each with its own question):", ""]
    for idx, p in enumerate(passages):
        facet = p.get("source_query") or query
        lines.append(f"[{idx}] Question: {facet}")
        lines.append(f"Passage: {p.get('content', '')}")
        lines.append("")
    try:
        response = openai_client.chat.completions.create(
            model=JUDGE_MODEL,
            messages=[
                {"role": "system", "content": _GRADE_SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(lines)},
            ],
            response_format={"type": "json_object"},
            **({"reasoning_effort": REASONING_EFFORT} if JUDGE_IS_REASONING
               else {"temperature": JUDGE_TEMPERATURE}),
        )
        raw = (response.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw).strip()
        data = json.loads(raw)
    except Exception as e:
        logging.error(f"grade batch failed: {e}")
        return None

    out = {}
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
            continue  # never surface a quote that is not verbatim in the passage
        out[idx] = {"relevance": relevance, "quote": exact}
    return out


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

    # Shard into concurrent batches. Same model, same prompt, same tokens — but a
    # third of the JSON per call, and grading is the pipeline's latency floor.
    # Batches are graded independently, then merged in the ORIGINAL passage order
    # so relevance ordering downstream is unaffected by which batch finished first.
    if len(passages) > GRADE_BATCH_SIZE:
        indexed = list(enumerate(passages))
        batches = [indexed[i:i + GRADE_BATCH_SIZE]
                   for i in range(0, len(indexed), GRADE_BATCH_SIZE)]
        results, failures = {}, 0
        # trace_out is NOT shared with the workers — each returns its own verdicts
        # and they are folded in on this thread, so the trace can't be interleaved
        # into an inconsistent state.
        with ThreadPoolExecutor(max_workers=min(len(batches), GRADE_MAX_WORKERS)) as pool:
            futures = {
                pool.submit(_grade_one_batch, query, [p for _, p in b]): b
                for b in batches
            }
            for fut in as_completed(futures):
                batch = futures[fut]
                verdicts = fut.result()
                if verdicts is None:
                    failures += 1
                    continue
                for local_idx, verdict in verdicts.items():
                    if 0 <= local_idx < len(batch):
                        results[batch[local_idx][0]] = verdict

        # Every batch failed -> this is a grader outage, not a set of rejections.
        # Fall back to the old whole-list behavior so nothing is silently lost.
        if failures == len(batches):
            logging.error("all grade batches failed; falling back to reranker chunks (no drop).")
            if trace_out is not None:
                trace_out["fallback"] = True
            return select_best_sentences(query, passages)

        kept = []
        for gi in sorted(results):
            verdict = results[gi]
            p = dict(passages[gi])
            p["grade_relevance"] = verdict["relevance"]
            p["best_sentence"] = verdict["quote"]
            kept.append(p)
            if trace_out is not None:
                trace_out.setdefault("kept_passages", []).append({
                    "title": p.get("title", ""),
                    "facet": p.get("source_query") or query,
                    "relevance": verdict["relevance"],
                })
        if trace_out is not None:
            for gi, p in enumerate(passages):
                if gi in results:
                    continue
                trace_out.setdefault("rejected_passages", []).append({
                    "title": p.get("title", ""),
                    "facet": p.get("source_query") or query,
                    # A passage in a failed batch is unverified, not judged
                    # irrelevant. Dropping it is the honest call for a
                    # quotes-only product, but the trace shouldn't claim the
                    # grader rejected it.
                    "reason": "not_answering" if failures == 0 else "unverified",
                })
            trace_out["kept"] = len(kept)
            trace_out["rejected"] = len(trace_out.get("rejected_passages", []))
        return kept

    system_prompt = _GRADE_SYSTEM_PROMPT
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
            **({"reasoning_effort": REASONING_EFFORT} if JUDGE_IS_REASONING
               else {"temperature": JUDGE_TEMPERATURE}),
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

def _passage_relevance(passage: Dict[str, Any]) -> float:
    """The best available relevance signal for a passage, most-trusted first.

    grade_relevance (LLM judged) > rerank_score (cross-encoder) > score (hybrid).
    The rerank_score rung matters for the deferred-grading path, where results are
    ordered before any grading has happened: without it, aggregation would fall
    all the way back to the raw hybrid score and discard the reranker's work.
    """
    for key in ("grade_relevance", "rerank_score", "score"):
        val = passage.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return 0.0


def aggregate_to_discourses(graded: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Collapse graded passages to one result per discourse (best passage wins)."""
    best_by_article = {}
    for passage in graded:
        article_id = passage.get("article_id", "")
        relevance = _passage_relevance(passage)
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
            # Weaviate UUID of the passage this citation came from. Carried so the
            # deferred-grading path can hand it back to /search/verify and have the
            # server re-fetch exactly this passage — keeping phase 2 stateless
            # rather than parking candidates in per-process memory.
            "passage_id": passage.get("_id", ""),
            # The sub-query that surfaced this passage. Sent to the client so a
            # deferred search can hand it back to /search/verify — grading each
            # passage against ITS OWN facet (not the raw user message) is what
            # keeps a multi-topic question from being judged on one topic.
            "facet": passage.get("source_query", ""),
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
