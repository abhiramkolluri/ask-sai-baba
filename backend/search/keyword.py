"""Keyword route — lexical search for bare 1-2 word topic queries.

A user who types "karma" or "truth" is browsing a topic, not asking a question,
and the semantic pipeline mishandles that at both ends: the router prompt's rule
(9) deliberately EXPANDS a bare topic into synonym facets, so the results are
discourses that are *thematically adjacent* rather than the ones that actually
use the word; and the grader is then asked "does this passage answer the
question" about something that poses no question. Both LLM calls are spent making
the answer worse.

So this route does the obvious thing instead: BM25 over the passages, keep only
the passages that literally contain the term, order by lexical score with titles
first, and return. No planner, no grader, no embedding call — roughly 1s.

Two outcomes:

  - "hit"  : at least KEYWORD_MIN_DISCOURSES distinct discourses literally use
             the term — return them.
  - "thin" : fewer than that. The corpus does not really cover this term as a
             *word* (rare terms, transliteration variants — "vairagya" where the
             corpus writes "detachment"), so the caller falls through to the
             semantic route rather than serve a thin literal result. Lexical
             search has no synonyms; that is the point of it, and also exactly
             why it needs an escape hatch.

The literal-verification step is the same promise the exact-phrase branch keeps:
BM25 is token-based and will rank a passage matching only one word of "inner
peace", so a claim of a keyword match is checked before it is made.
"""

import logging

from .config import KEYWORD_MIN_DISCOURSES, KEYWORD_OVERFETCH
from .ranking import aggregate_to_discourses, select_best_sentences
from .retrieval import phrase_in_text, search_passages_keyword


def keyword_search(term, limit=5):
    """Lexically search the corpus for `term`.

    Returns ``{"status": "hit"|"thin", "discourses": [...], "literal_passages": n}``.
    Discourse dicts come out of ``aggregate_to_discourses``, so they are the exact
    shape the semantic route emits — format_docs, follow-up grounding and the
    frontend cards all just work.

    Raises PipelineServiceError (from search_passages_keyword) when Weaviate is
    unreachable; the caller distinguishes that from an empty corpus match.
    """
    candidates = search_passages_keyword(term, overfetch=KEYWORD_OVERFETCH)

    # Keep only passages that genuinely contain the term. phrase_in_text tolerates
    # punctuation between words, so "inner peace" still matches "inner, peace" and
    # a title like "Love all: Serve all" matches "love all".
    literal = [
        p for p in candidates
        if phrase_in_text(term, p.get("content", "")) or phrase_in_text(term, p.get("title", ""))
    ]

    # A discourse whose TITLE names the term is what the user typing that word is
    # looking for, whatever BM25 thought of the body. Sort on (title match, score)
    # so lexical order still decides within each band.
    literal.sort(key=lambda p: (phrase_in_text(term, p.get("title", "")), p.get("score", 0.0)),
                 reverse=True)

    # Rewrite the score as RANK, descending into (0, 1]. Two reasons, and the
    # ordering above depends on the second:
    #
    # 1. Raw BM25 is unbounded and on a completely different scale from the LLM
    #    grader's relevance, but it flows into trace.results.top_relevance,
    #    ranking._passage_relevance and the frontend as if it were one. Order is
    #    the only thing BM25 tells us honestly, so order is all we encode.
    # 2. aggregate_to_discourses re-sorts by score, so a title boost applied only
    #    as a sort key is silently thrown away one line later. Folding the final
    #    order into the score is what makes it survive.
    n = len(literal)
    for i, p in enumerate(literal):
        p["score"] = (n - i) / n

    discourses = aggregate_to_discourses(literal, limit)

    # Threshold on DISCOURSES, not passages: a common word can put 40 passages
    # from three discourses at the top, which is a thin result dressed up as a
    # thick one. This is also what lets assess_quality call the route "strong"
    # without inventing a BM25-scale relevance bar.
    if len(discourses) < KEYWORD_MIN_DISCOURSES:
        logging.info("Keyword route thin for %r (%d literal passages, %d discourses) — "
                     "falling through to semantic.", term, len(literal), len(discourses))
        return {"status": "thin", "discourses": [], "literal_passages": len(literal)}

    # One reranker call over sentence windows, no LLM: picks the chunk of each
    # passage where the term is actually in use, rather than the passage's opening
    # lines. Degrades to the leading chunk if the reranker is unavailable.
    discourses = select_best_sentences(term, discourses)

    return {"status": "hit", "discourses": discourses, "literal_passages": len(literal)}
