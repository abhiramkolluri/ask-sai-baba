"""The search package — retrieval & ranking pipeline over the discourse corpus.

A user query flows through four stages, one module each:

    query_planning  → 1–N standalone, glossed sub-queries
    retrieval       → hybrid BM25 + vector candidates (+ legacy Article reads)
    ranking         → Cohere rerank, LLM extractive grade, aggregate to discourses
    pipeline        → orchestration that sequences the above into entrypoints

``config`` holds the shared OpenAI client and every tunable constant.

This ``__init__`` re-exports the public API so callers outside the package can
simply write ``from search import search_browse`` without knowing which stage a
symbol lives in. (Note: to monkeypatch ``HYBRID_ALPHA`` for the alpha sweep,
import the submodule directly — ``from search import retrieval`` — and set
``retrieval.HYBRID_ALPHA``; re-exported names are separate bindings.)

Request flow:
    Frontend → API Gateway → app.py → search package → chat.py + persistence.py
"""

# Shared client + the constants external callers (e.g. the eval harness) reference.
from .config import (
    openai_client,
    COHERE_API_KEY,
    PASSAGE_OVERFETCH,
    RERANK_KEEP,
    HYBRID_ALPHA,
    GRADE_MIN_RELEVANCE,
)

# Stage 1 — query planning & routing.
from .query_planning import expand_short_query, plan_queries, plan_search, SearchPlan

# Stage 2 — Weaviate reads & rank fusion.
from .retrieval import (
    check_vector_store_health,
    get_embedding,
    search_passages,
    search_exact,
    search_browse_articles_legacy,
    get_full_article,
    build_passage_filter,
)

# Structured-search support — corpus catalog and the pure metadata listing path.
from .catalog import get_catalog, canonical_book
from .listing import list_discourses

# Collections browsing — grouped corpus index + per-collection chapter lists.
from .collections_index import get_collections_index, list_collection_chapters

# Stage 3 — rerank, grade, aggregate.
from .ranking import (
    rerank_passages,
    select_best_sentences,
    grade_and_quote_passages,
    grade_passages,
    aggregate_to_discourses,
)

# Stage 4 — orchestration entrypoints + small helpers.
from .pipeline import (
    extract_quoted_phrase,
    format_docs,
    search_browse,
    generate_followups,
    search,
)

__all__ = [
    "openai_client",
    "COHERE_API_KEY",
    "PASSAGE_OVERFETCH",
    "RERANK_KEEP",
    "HYBRID_ALPHA",
    "GRADE_MIN_RELEVANCE",
    "expand_short_query",
    "plan_queries",
    "plan_search",
    "SearchPlan",
    "get_catalog",
    "canonical_book",
    "list_discourses",
    "get_collections_index",
    "list_collection_chapters",
    "build_passage_filter",
    "check_vector_store_health",
    "get_embedding",
    "search_passages",
    "search_exact",
    "search_browse_articles_legacy",
    "get_full_article",
    "rerank_passages",
    "select_best_sentences",
    "grade_and_quote_passages",
    "grade_passages",
    "aggregate_to_discourses",
    "extract_quoted_phrase",
    "format_docs",
    "search_browse",
    "generate_followups",
    "search",
]
