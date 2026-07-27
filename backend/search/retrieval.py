"""Stage 2 of the pipeline — read from Weaviate and fuse ranked lists.

Everything that actually *fetches* from the vector store lives here: the hybrid
(BM25 + vector) passage search that feeds the pipeline, the legacy ``Article``
near_text/exact-match helpers, single-article fetch for the blog view, the OpenAI
embedding helper, the store-health check, and the Reciprocal Rank Fusion used to
merge per-facet result lists.

Reranking, grading, and aggregation happen *downstream* in ``ranking.py`` — this
module only reads and fuses; it does not judge relevance.

NOTE on ``HYBRID_ALPHA``: it is imported into this module's namespace and read by
``search_passages`` at call time. The ``eval_transliteration.py`` tuning harness
sweeps alpha by patching ``retrieval.HYBRID_ALPHA`` — that is the binding this
function actually reads.
"""

import re
import logging
from typing import List, Dict, Any

import weaviate
from weaviate_client import get_client
from weaviate.classes.query import Filter, MetadataQuery

from .config import openai_client, HYBRID_ALPHA, PASSAGE_OVERFETCH, EXCLUDED_COLLECTIONS, WEAVIATE_RETRY_ATTEMPTS, PASSAGE_COLLECTION
from .resilience import with_retries


# ===========================================================================
# Store health & embeddings
# ===========================================================================

def check_vector_store_health():
    """Check if Weaviate is properly initialized and has data."""
    try:
        client = get_client()
        if client and client.is_live():
            articles = client.collections.get("Article")
            count = articles.aggregate.over_all(total_count=True)
            return count.total_count > 0
        return False
    except Exception as e:
        logging.error(f"Vector store health check failed: {e}")
        return False

def get_embedding(text):
    """Generate an embedding for the given text using OpenAI directly."""
    if not text or not isinstance(text, str):
        return None
    try:
        response = openai_client.embeddings.create(
            model="text-embedding-3-large",
            input=text
        )
        return response.data[0].embedding
    except Exception as e:
        logging.error(f"Error generating embedding: {e}")
        return None


# ===========================================================================
# Hybrid passage search & rank fusion
# ===========================================================================

def _passage_filters(occasion: str = None, metadata_filter=None):
    """Build the Weaviate filter for passage search.

    Always excludes EXCLUDED_COLLECTIONS (administrative documents, not
    discourses). When `occasion` is given (from the planner's occasion routing,
    e.g. "Dasara"), additionally restrict to passages whose occasion metadata
    contains it — wildcards make "Summer Course" match the corpus's longer
    "Summer Course in Indian Culture and Spirituality".

    `metadata_filter` is an already-composed Filter from build_passage_filter
    (book / volume / chapter / year / location / occasion). It is ANDed in
    rather than replacing anything: the collection exclusions are a correctness
    invariant, not a preference, and must survive every structured filter.
    """
    combined = None
    for name in EXCLUDED_COLLECTIONS:
        f = Filter.by_property("collection_name").not_equal(name)
        combined = f if combined is None else combined & f
    if occasion:
        f = Filter.by_property("occasion").like(f"*{occasion.lower()}*")
        combined = f if combined is None else combined & f
    if metadata_filter is not None:
        combined = metadata_filter if combined is None else combined & metadata_filter
    return combined


def build_passage_filter(filters: Dict[str, Any]):
    """Compose a Weaviate Filter from the router's validated `filters` dict, or
    None when there is nothing to filter on.

    Reads the normalized metadata backfilled by the ingestion scripts:
    `book` is FIELD-tokenized so equality is whole-name exact; `chapter_index`
    is stored 0-based while the router speaks 1-based chapters (converted here);
    location/occasion match word tokens case-insensitively, which tolerates
    the corpus's messy values ('Brindavan, KA', 'Dasara, Vijayadasami').
    """
    if not filters:
        return None
    clauses = []
    if filters.get("book"):
        clauses.append(Filter.by_property("book").equal(filters["book"]))
    if filters.get("volume") is not None:
        clauses.append(Filter.by_property("volume").equal(filters["volume"]))
    if filters.get("chapter_start") is not None:
        clauses.append(Filter.by_property("chapter_index").greater_or_equal(filters["chapter_start"] - 1))
    if filters.get("chapter_end") is not None:
        clauses.append(Filter.by_property("chapter_index").less_or_equal(filters["chapter_end"] - 1))
    if filters.get("year_start") is not None:
        clauses.append(Filter.by_property("year").greater_or_equal(filters["year_start"]))
    if filters.get("year_end") is not None:
        clauses.append(Filter.by_property("year").less_or_equal(filters["year_end"]))
    for prop in ("location", "occasion"):
        if filters.get(prop):
            clauses.append(Filter.by_property(prop).contains_all(filters[prop].split()))
    if not clauses:
        return None
    combined = clauses[0]
    for clause in clauses[1:]:
        combined = combined & clause
    return combined


def search_passages(query: str, overfetch: int = PASSAGE_OVERFETCH, occasion: str = None,
                    filters=None) -> List[Dict[str, Any]]:
    """Hybrid (BM25 + vector) search over the Passage collection.

    The query is expected to be already prepared (glossed/distilled) by
    `plan_queries`; this function does not re-expand it. `occasion` optionally
    restricts results to a given occasion/festival via metadata filter, and
    `filters` is an optional composed Filter (see build_passage_filter) that
    constrains the candidate set by book/chapter/year/location metadata.

    Transient Weaviate connection failures are retried; if they persist this
    raises ``PipelineServiceError`` rather than returning ``[]`` — an empty list
    means "the corpus had no matches", a raised error means "we couldn't reach
    the search service", and the two must not be conflated (a blip previously
    surfaced to users as a false "no discourses found").
    """
    def _query():
        client = get_client()
        if not client:
            # Treated as transient: get_client rebuilds the connection on retry.
            raise RuntimeError("Weaviate client not available for passage search.")
        passages = client.collections.get(PASSAGE_COLLECTION)
        response = passages.query.hybrid(
            query=query,
            alpha=HYBRID_ALPHA,
            limit=overfetch,
            query_properties=["content", "title"],
            filters=_passage_filters(occasion, filters),
            return_metadata=MetadataQuery(score=True)
        )
        results = []
        for obj in response.objects:
            props = obj.properties
            results.append({
                "_id": str(obj.uuid),
                "article_id": props.get("article_id", ""),
                "chunk_index": props.get("chunk_index", 0),
                "content": props.get("content", ""),
                "title": props.get("title", ""),
                "location": props.get("location", ""),
                "occasion": props.get("occasion", ""),
                "link": props.get("link", ""),
                "collection_name": props.get("collection_name", ""),
                "date_authored": props.get("date_authored", ""),
                "score": obj.metadata.score or 0.0,
            })
        return results

    # Raises PipelineServiceError when retries are exhausted; the caller
    # (search_browse) distinguishes this from a genuinely empty result.
    return with_retries(_query, attempts=WEAVIATE_RETRY_ATTEMPTS, what="Passage hybrid search")

def fetch_passages_by_ids(passage_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch passages by Weaviate UUID, returned as {passage_id: passage_dict}.

    This is what keeps the two-phase search stateless: phase 1 hands the client a
    set of passage ids, and phase 2 re-reads exactly those passages instead of
    relying on server-side memory that would not survive a second EB instance (or
    a restart) between the two calls.

    One round trip via an id filter rather than N fetch_object_by_id calls — at
    ~15 passages the per-call latency would otherwise dominate phase 2.
    """
    if not passage_ids:
        return {}

    def _query():
        client = get_client()
        if not client:
            raise RuntimeError("Weaviate client not available for passage fetch.")
        passages = client.collections.get(PASSAGE_COLLECTION)
        response = passages.query.fetch_objects(
            filters=Filter.by_id().contains_any(list(passage_ids)),
            limit=len(passage_ids),
        )
        out = {}
        for obj in response.objects:
            props = obj.properties
            out[str(obj.uuid)] = {
                "_id": str(obj.uuid),
                "article_id": props.get("article_id", ""),
                "chunk_index": props.get("chunk_index", 0),
                "content": props.get("content", ""),
                "title": props.get("title", ""),
                "location": props.get("location", ""),
                "occasion": props.get("occasion", ""),
                "link": props.get("link", ""),
                "collection_name": props.get("collection_name", ""),
                "date_authored": props.get("date_authored", ""),
                "score": 0.0,
            }
        return out

    return with_retries(_query, attempts=WEAVIATE_RETRY_ATTEMPTS, what="Passage fetch by id")


def _rrf_merge(ranked_lists, k: int = 60) -> List[Dict[str, Any]]:
    """Reciprocal Rank Fusion over several best-first passage lists. Dedupes by
    passage `_id`; each kept passage retains the `source_query` from its best-ranked
    appearance. Returns one list ordered by fused score."""
    scores = {}
    best = {}  # _id -> (best_rank, passage)
    for lst in ranked_lists:
        for rank, p in enumerate(lst):
            pid = p.get("_id")
            if not pid:
                continue
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
            if pid not in best or rank < best[pid][0]:
                best[pid] = (rank, p)
    return [best[pid][1] for pid in sorted(scores, key=lambda pid: (-scores[pid], pid))]


# ===========================================================================
# Legacy Article reads — exact match, near_text, single-article fetch
# ===========================================================================

def phrase_in_text(phrase: str, text: str) -> bool:
    """True when `phrase` appears in `text` as a contiguous phrase, ignoring
    case and punctuation/whitespace between words (so 'love all serve all'
    matches the title 'Love all: Serve all').

    Exists because search_exact's Weaviate filters are TOKEN-based — they can
    return bag-of-words matches that never contain the phrase. This verifies
    the promise the exact-phrase feature makes to the user. It is deliberately
    looser than ranking._locate_verbatim (which tolerates only whitespace):
    quote extraction must be verbatim, but a user-typed phrase shouldn't fail
    on a colon or comma the corpus happens to use.
    """
    if not phrase or not text:
        return False
    tokens = [t.strip(",.:;!?\"'") for t in phrase.split()]
    tokens = [t for t in tokens if t]  # drop tokens that were pure punctuation
    if not tokens:
        return False
    pattern = r"[^\w]+".join(re.escape(t) for t in tokens)
    return re.search(pattern, text, re.IGNORECASE) is not None


def search_exact(
    query: str,
    limit: int = 5,
    full_match_score: float = 1.0,
    partial_match_score: float = 0.9
) -> List[Dict[str, Any]]:
    """Search articles using exact string matching in title or content."""
    try:
        client = get_client()
        if not client:
            logging.error("Weaviate client not available for exact search.")
            return []

        articles = client.collections.get("Article")
        # First try exact match in title
        response = articles.query.fetch_objects(
            filters=Filter.by_property("title").equal(query),
            limit=limit
        )

        results = []
        for obj in response.objects:
            results.append({
                "_id": str(obj.uuid),
                "title": obj.properties.get("title", "Untitled"),
                "content": obj.properties.get("content", ""),
                "score": full_match_score,  # Exact match gets perfect score
                "location": obj.properties.get("location", ""),
                "occasion": obj.properties.get("occasion", ""),
                "link": obj.properties.get("link", ""),
                "collection": obj.properties.get("collection_name", ""),
            })

        # If no exact title matches, search for substring in content
        if not results:
            response = articles.query.fetch_objects(
                filters=Filter.by_property("content").contains_any([query]),
                limit=limit
            )

            for obj in response.objects:
                results.append({
                    "_id": str(obj.uuid),
                    "title": obj.properties.get("title", "Untitled"),
                    "content": obj.properties.get("content", ""),
                    "score": partial_match_score,  # Substring match gets high score
                    "location": obj.properties.get("location", ""),
                    "occasion": obj.properties.get("occasion", ""),
                    "link": obj.properties.get("link", ""),
                    "collection": obj.properties.get("collection_name", ""),
                })

        return results[:limit]  # Ensure we don't exceed limit
    except Exception as e:
        logging.error(f"Weaviate exact search failed: {e}")
        return []

def search_browse_articles_legacy(query: str, limit: int = 5, exact_phrase: str = None) -> List[Dict[str, Any]]:
    """Search articles using Weaviate's native near_text capabilities.

    Verbatim copy of the original search_browse() body, preserved as a rollback
    path before Prompt 5 replaces search_browse with the passage pipeline.
    """
    # If an exact phrase was extracted from a quoted user query, attempt exact
    # matching first (title priority, then content). Only fall through to
    # semantic search if no exact results are found, in which case we search
    # on just the phrase rather than the full raw query sentence.
    if exact_phrase:
        exact_results = search_exact(exact_phrase, limit=limit)
        if exact_results:
            return exact_results
        # No exact matches found — run semantic search on the phrase alone,
        # not the full raw query, so the vector search is focused.
        query = exact_phrase

    try:
        client = get_client()
        if not client:
            logging.error("Weaviate client not available for search.")
            return []

        articles = client.collections.get("Article")
        response = articles.query.near_text(
            query=query,
            limit=limit,
            return_metadata=weaviate.classes.query.MetadataQuery(distance=True)
        )

        results = []
        for obj in response.objects:
            results.append({
                "_id": str(obj.uuid),
                "title": obj.properties.get("title", "Untitled"),
                "content": obj.properties.get("content", ""),
                "score": 1 - (obj.metadata.distance or 0),
                "location": obj.properties.get("location", ""),
                "occasion": obj.properties.get("occasion", ""),
                "link": obj.properties.get("link", ""),
                "collection": obj.properties.get("collection_name", ""),
                "date": obj.properties.get("date", ""),
            })
        return results
    except Exception as e:
        logging.error(f"Weaviate search failed: {e}")
        return []

def get_full_article(id, collection=None):
    """Retrieve full article by UUID or slug from Weaviate."""
    try:
        client = get_client()
        articles = client.collections.get("Article")

        obj = None
        # Try UUID lookup first
        try:
            import uuid
            uuid_obj = uuid.UUID(id)
            obj = articles.query.fetch_object_by_id(uuid_obj)
        except ValueError:
            # Not a UUID — try slug-based lookup by searching title similarity
            logging.info(f"Non-UUID id '{id}', attempting slug-based search")
            # Convert slug to search query: replace hyphens with spaces, strip trailing numbers
            search_query = re.sub(r'\d+$', '', id).replace('-', ' ').strip()
            if search_query:
                response = articles.query.near_text(
                    query=search_query,
                    limit=1,
                    return_metadata=MetadataQuery(distance=True)
                )
                if response.objects:
                    obj = response.objects[0]

        if not obj:
            return None

        props = obj.properties or {}
        volume = props.get("volume")
        chapter_index = props.get("chapter_index")
        year = props.get("year")
        article = {
            "_id": str(obj.uuid),
            "title": props.get("title", ""),
            "content": props.get("content", ""),
            "location": props.get("location", ""),
            "occasion": props.get("occasion", ""),
            "link": props.get("link", ""),
            "collection": props.get("collection_name", ""),
            "date": props.get("date", "") or "",
            # Normalized position metadata for the reader's chapter line
            # ("Vol 14 · Discourse 10", "Chapter 3").
            "book": props.get("book", "") or "",
            "volume": int(volume) if volume is not None else None,
            "chapter_index": int(chapter_index) if chapter_index is not None else None,
            "year": int(year) if year is not None else None,
        }

        # Convert to markdown format
        markdown_article = f"# {article['title']}\n\n"
        markdown_article += f"**Location:** {article['location']}\n\n"
        markdown_article += f"**Occasion:** {article['occasion']}\n\n"
        markdown_article += f"**Collection:** {article['collection']}\n\n"
        markdown_article += f"**Link:** [{article['link']}]({article['link']})\n\n"
        markdown_article += f"## Content:\n\n{article['content']}\n"
        article['markdown_format'] = markdown_article

        return article
    except Exception as e:
        logging.error(f"Error fetching article by id: {e}")
        return None
