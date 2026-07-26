"""Structured knowledge route (Router v2 / Phase 2).

Factual / named-text / org-doctrine questions can't be answered by thematic
semantic search — "Who was Swami's mother?" returns generic mother-themed
discourses, "Tripura Rahasyam" collides with the homonymous 'three cities'. This
module looks the question up in the curated Entity collection (see
ingest_entities.py) and returns one of three outcomes:

  - "hit"  : a matching entity that points at canonical Article(s) — return those
             discourses (with an answering quote) instead of a thematic guess.
  - "gap"  : a matching entity we know the corpus does NOT cover — abstain
             honestly (empty + KB_KNOWN_GAP) rather than returning spurious matches.
  - "miss" : no confident entity match — the caller falls back to the semantic
             route so we never dead-end.

We never fabricate a fact: a "gap" returns nothing to say plus an honest note; a
"hit" returns Swami's own discourses.
"""

import json
import logging

from weaviate_client import get_client
from weaviate.classes.query import MetadataQuery
from .ranking import select_best_sentences
from .resilience import with_retries, PipelineServiceError
from .config import WEAVIATE_RETRY_ATTEMPTS

# Minimum hybrid score for an Entity match to be trusted; below this we treat the
# question as a "miss" and fall back to semantic search rather than force a guess.
ENTITY_MATCH_MIN_SCORE = 0.5

# Minimum share of the QUESTION's content words that the matched entity must
# account for. Calibrated against the golden set's exact-discourse cases, which
# name a discourse title containing an incidental concept word.
ENTITY_MIN_QUERY_COVERAGE = 0.4

# Words too generic to signal an entity match (so "teachings of the Gita" doesn't
# match an entity purely on "teachings"/"the").
_STOPWORDS = {
    "the", "of", "a", "an", "and", "or", "is", "are", "was", "what", "who", "how",
    "why", "when", "where", "to", "in", "on", "for", "about", "does", "did", "can",
    "swami", "baba", "sathya", "sai", "discourses", "discourse", "teachings", "teaching",
}


def _content_words(text):
    """Significant lowercase words of a string, for entity-match overlap checks."""
    import re
    return {w for w in re.findall(r"[a-z]+", (text or "").lower())
            if len(w) > 2 and w not in _STOPWORDS}


def _article_to_result(obj):
    """Shape an Article object like the pipeline's discourse-result dicts."""
    p = obj.properties
    content = p.get("content", "") or ""
    return {
        "_id": str(obj.uuid),
        "title": p.get("title", ""),
        "matched_passage": content,
        "passage_index": 0,
        "content": content,
        "best_sentence": "",  # filled by select_best_sentences below
        "score": 1.0,
        "location": p.get("location", ""),
        "occasion": p.get("occasion", ""),
        "link": p.get("link", ""),
        "collection": p.get("collection_name", ""),
        "date_authored": p.get("date", ""),
    }


def lookup_entity(query, entities=None):
    """Look the question up in the Entity collection.

    Returns a dict: {"status": "hit"|"gap"|"miss", "entity": name|None,
    "results": [...]}. On any Weaviate failure raises PipelineServiceError so the
    caller surfaces an honest service error (never a false empty)."""
    # Search by the raw question plus any named entities the router pulled out.
    search_text = query
    if entities:
        search_text = query + " " + " ".join(entities)

    def _run():
        client = get_client()
        if not client:
            raise RuntimeError("Weaviate client unavailable for entity lookup")
        col = client.collections.get("Entity")
        return col.query.hybrid(
            query=search_text,
            limit=1,
            return_metadata=MetadataQuery(score=True),
        )

    resp = with_retries(_run, attempts=WEAVIATE_RETRY_ATTEMPTS, what="Entity lookup")
    if not resp.objects:
        return {"status": "miss", "entity": None, "results": []}

    match = resp.objects[0]
    score = match.metadata.score or 0.0
    if score < ENTITY_MATCH_MIN_SCORE:
        return {"status": "miss", "entity": None, "results": []}

    props = match.properties
    name = props.get("name", "")

    # Precision guard against BM25 false matches: an entity is only accepted when
    # its name/aliases share a real content word with the question. Without this,
    # "teachings of the Gita" can hybrid-match an unrelated entity on common words
    # and get wrongly routed to the structured lookup. On no overlap -> miss ->
    # semantic route.
    entity_words = _content_words(name + " " + (props.get("aliases") or ""))
    query_words = _content_words((query or "") + " " + " ".join(entities or []))
    if not (entity_words & query_words):
        return {"status": "miss", "entity": None, "results": []}

    # Coverage guard: the matched entity must account for a real SHARE of the
    # question, not just appear inside it. Bare overlap was enough when the KB
    # held a handful of curated rows; with ~1,000 concept entities it is not.
    #
    # "the discourse titled Ahamkara Causes Ashanti" names a DISCOURSE, but
    # contains the concept entity "Ahamkara" — one word out of four — so the
    # structured route answered a title lookup with an essay on ego. A question
    # genuinely about an entity is mostly that entity ("what is Ahamkara" -> 1.0);
    # a title that happens to contain one scores far lower. Below the bar we fall
    # through to semantic search, which is what finds discourses by title.
    coverage = len(entity_words & query_words) / max(len(query_words), 1)
    if coverage < ENTITY_MIN_QUERY_COVERAGE:
        logging.info(
            f"entity {name!r} matched {query!r} at coverage {coverage:.2f} "
            f"< {ENTITY_MIN_QUERY_COVERAGE} -> falling through to semantic"
        )
        return {"status": "miss", "entity": None, "results": []}

    in_corpus = bool(props.get("in_corpus"))
    try:
        article_ids = json.loads(props.get("canonical_article_ids") or "[]")
    except Exception:
        article_ids = []

    # Known gap: the corpus doesn't cover this — abstain honestly.
    if not in_corpus or not article_ids:
        return {"status": "gap", "entity": name, "results": []}

    # Hit: fetch the canonical Article(s) and attach an answering quote.
    def _fetch():
        client = get_client()
        articles = client.collections.get("Article")
        out = []
        for aid in article_ids:
            try:
                obj = articles.query.fetch_object_by_id(aid)
            except Exception as e:
                logging.warning(f"entity canonical article {aid} fetch failed: {e}")
                obj = None
            if obj is not None:
                out.append(_article_to_result(obj))
        return out

    results = with_retries(_fetch, attempts=WEAVIATE_RETRY_ATTEMPTS, what="Entity article fetch")
    if not results:
        # The entity pointed at articles that no longer exist -> treat as a miss
        # so the semantic route still tries.
        return {"status": "miss", "entity": name, "results": []}

    results = select_best_sentences(query, results)
    return {"status": "hit", "entity": name, "results": results}
