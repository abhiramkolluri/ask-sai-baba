"""Listing route — return discourses by METADATA, in order, with no topic.

Covers both shapes of "just show me the discourses":
  - within a book:  "the first five chapters of Geeta Vahini", "chapter 3 of
    Dhyana Vahini", "discourse 10 of Sathya Sai Speaks volume 14"
  - across the corpus: "all discourses from 1976", "Dasara discourses",
    "discourses given in Brindavan"

There is no question to answer here, so the passage pipeline (retrieve → rerank
→ grade) is bypassed entirely: the Article collection is filtered on normalized
metadata and returned in chapter or date order. Results are shaped exactly like
``aggregate_to_discourses`` output so the frontend cards, ``format_docs`` and
follow-up grounding all work unchanged. The snippet is the discourse's opening
sentences — nothing was asked about the content, so there is no answering quote
to extract, and it must never be presented as one.

TWO PROPERTIES WORTH KEEPING (each was a regression once):

1. A Weaviate failure RAISES rather than returning []. "No such book" and "we
   couldn't reach the search service" are different answers and must never be
   conflated — an outage previously surfaced to users as a confident claim that
   we don't hold a book we do hold.
2. Loosely-spelled book names resolve. The router is grounded in the corpus's
   real book list (see catalog.py) and normally emits "Geeta Vahini" for "the
   Gita Vahini", but ``canonical_book`` only matches exact-lowercase, so any
   spelling it doesn't recognise still arrives verbatim and an exact `book`
   equality filter matches nothing. ``_resolve_book_name`` catches those.
"""

import re
import logging
from typing import Any, Dict

from weaviate.classes.query import Filter, Sort

from weaviate_client import get_client
from .config import (LISTING_MAX_RESULTS, LISTING_SNIPPET_SENTENCES,
                     WEAVIATE_RETRY_ATTEMPTS)
from .ranking import _split_sentences
from .resilience import with_retries

# Article props fetched for listing cards. `content` is needed for the snippet
# but is truncated immediately — full chapters never leave the backend.
_LISTING_PROPS = [
    "title", "content", "location", "occasion", "link",
    "collection_name", "date", "book", "volume", "chapter_index", "year",
]

# When date-sorting, matches beyond the returned page still need to be seen to
# sort correctly (Weaviate can't sort on our parsed date), so fetch this many.
_DATE_SORT_FETCH = 300


def build_article_filter(filters: Dict[str, Any]):
    """The Article-side analogue of retrieval.build_passage_filter (same
    validated filter vocabulary, same 1-based -> 0-based chapter conversion),
    or None when there is nothing to filter on."""
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


# ---------------------------------------------------------------------------
# Loose book-name resolution (fallback only — never on the happy path)
# ---------------------------------------------------------------------------

def _translit_key(name):
    """Collapse a romanized title to a comparison key.

    Sanskrit/Telugu transliteration varies mostly in vowels and in a trailing
    'h' after consonants — Gita / Geeta / Geetha are the same book. Dropping
    vowels and h leaves "gtvhn" for all three, so they compare equal.
    """
    return re.sub(r"[^a-z]", "", re.sub(r"[aeiouh]", "", (name or "").lower()))


def _known_book_names():
    """Distinct book/collection names in the corpus. Only called on a miss."""
    def _fetch_names():
        client = get_client()
        if not client:
            raise RuntimeError("Weaviate client unavailable for book resolve")
        articles = client.collections.get("Article")
        names = set()
        for o in articles.query.fetch_objects(
            limit=4000, return_properties=["collection_name", "book"]
        ).objects:
            p = o.properties or {}
            for key in ("book", "collection_name"):
                v = (p.get(key) or "").strip()
                if v:
                    names.add(v)
        return names

    try:
        return with_retries(_fetch_names, attempts=WEAVIATE_RETRY_ATTEMPTS,
                            what="Book name resolve")
    except Exception as e:
        logging.warning(f"book-name resolve failed: {e}")
        return set()


def _resolve_book_name(book):
    """Map a loosely-spelled book onto the corpus's own spelling, or None."""
    want = _translit_key(book)
    if not want:
        return None
    best = None
    for name in _known_book_names():
        key = _translit_key(name)
        # Equal keys, or the query is a clean substring of a longer title
        # ("Prema Vahini" inside "Prema Vahini Vol 2").
        if key == want or (len(want) >= 4 and want in key):
            if best is None or len(name) < len(best):
                best = name
    return best


# ---------------------------------------------------------------------------
# Cards + the listing itself
# ---------------------------------------------------------------------------

def _snippet(content: str) -> str:
    return " ".join(_split_sentences(content or "")[:LISTING_SNIPPET_SENTENCES])


def _card(obj) -> Dict[str, Any]:
    """One listing result in the exact shape aggregate_to_discourses emits, so
    the frontend cards, format_docs, and follow-up grounding all just work."""
    props = obj.properties or {}
    snippet = _snippet(props.get("content", ""))
    date = props.get("date", "") or ""
    return {
        "_id": str(obj.uuid),
        "title": props.get("title", ""),
        "matched_passage": snippet,
        "passage_index": 0,
        "content": snippet,
        "best_sentence": snippet,   # a preview, NOT an answering quote
        "score": 1.0,               # metadata matches are exact, not ranked
        "location": props.get("location", ""),
        "occasion": props.get("occasion", ""),
        "link": props.get("link", ""),
        "collection": props.get("collection_name", ""),
        "date_authored": date,
        "date": date,
        # The frontend renders "Chapter N" from this; dropping it silently
        # de-numbers every chapter listing.
        "chapter_index": props.get("chapter_index"),
    }


def list_discourses(filters: Dict[str, Any], sort: str = None, limit: int = None,
                    order: str = "first") -> Dict[str, Any]:
    """Return the discourses a listing plan describes, in reading order.

    Returns ``{"status": "found"|"not_found", "book": <resolved>|None,
    "results": [...]}``. Raises ``PipelineServiceError`` on a Weaviate failure
    so the caller reports an honest service error rather than a false empty.

    `order` is "first" | "last" | "all" — "the last 3 chapters of Dharma Vahini"
    takes the tail while staying in reading order.
    """
    filters = dict(filters or {})
    flt = build_article_filter(filters)
    if flt is None:
        return {"status": "not_found", "book": None, "results": []}

    n = min(limit or LISTING_MAX_RESULTS, LISTING_MAX_RESULTS)
    sort_by = sort or ("chapter" if filters.get("book") else "date")
    # "last"/"all" need the whole set before slicing, and date order is computed
    # locally (the corpus stores dates as text), so over-fetch except on the one
    # case Weaviate can bound server-side.
    fetch = n if (order == "first" and sort_by == "chapter") else _DATE_SORT_FETCH

    def _fetch(active_filter):
        client = get_client()
        if not client:
            raise RuntimeError("Weaviate client unavailable for listing")
        articles = client.collections.get("Article")
        kwargs = {"filters": active_filter, "limit": fetch,
                  "return_properties": _LISTING_PROPS}
        if sort_by == "chapter":
            kwargs["sort"] = Sort.by_property("chapter_index", ascending=True)
        return articles.query.fetch_objects(**kwargs).objects

    objs = with_retries(lambda: _fetch(flt), attempts=WEAVIATE_RETRY_ATTEMPTS,
                        what="Listing")

    # Miss on a named book: the router may have emitted a spelling the corpus
    # does not use ("Gita Vahini" vs "Geeta Vahini"), which an exact `book`
    # equality filter matches nowhere. Resolve and retry once.
    if not objs and filters.get("book"):
        alt = _resolve_book_name(filters["book"])
        if alt and alt.lower() != str(filters["book"]).lower():
            logging.info(f"listing: {filters['book']!r} resolved to corpus spelling {alt!r}")
            filters["book"] = alt
            retry_filter = build_article_filter(filters)
            objs = with_retries(lambda: _fetch(retry_filter),
                                attempts=WEAVIATE_RETRY_ATTEMPTS,
                                what="Listing (resolved)")

    if not objs:
        return {"status": "not_found", "book": filters.get("book"), "results": []}

    if sort_by != "chapter":
        from metadata_norm import date_sort_key
        objs = sorted(objs, key=lambda o: date_sort_key((o.properties or {}).get("date", "")))

    if order == "all":
        chosen = objs[:LISTING_MAX_RESULTS]
    elif order == "last":
        chosen = objs[-n:]          # tail, still in reading order
    else:
        chosen = objs[:n]

    resolved = filters.get("book") or (chosen[0].properties or {}).get("collection_name")
    return {"status": "found", "book": resolved, "results": [_card(o) for o in chosen]}
