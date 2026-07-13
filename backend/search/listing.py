"""Pure metadata listings — "the first five discourses of Geeta Vahini",
"all discourses from 1976".

When plan_search classifies a message as a listing, there is no topic to
retrieve, rerank, or grade against, so the passage pipeline is bypassed
entirely: the Article collection is filtered on the normalized metadata and
the matches are returned in chapter or date order, shaped exactly like
``aggregate_to_discourses`` output so the frontend and chat layer render them
unchanged. The snippet shown is simply the discourse's opening sentences —
nothing was asked about the content, so there is no answering quote to
extract.
"""

import logging
from typing import Any, Dict, List

from weaviate.classes.query import Filter, Sort

from .config import LISTING_MAX_RESULTS, LISTING_SNIPPET_SENTENCES
from .ranking import _split_sentences

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
    validated SearchPlan.filters vocabulary, same 1-based -> 0-based chapter
    conversion), or None when there is nothing to filter on."""
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


def _snippet(content: str) -> str:
    sentences = _split_sentences(content or "")
    return " ".join(sentences[:LISTING_SNIPPET_SENTENCES])


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
        "best_sentence": snippet,
        "score": 1.0,  # metadata matches are exact, not ranked
        "location": props.get("location", ""),
        "occasion": props.get("occasion", ""),
        "link": props.get("link", ""),
        "collection": props.get("collection_name", ""),
        "date_authored": date,
        "date": date,
    }


def list_discourses(plan, limit_default: int = LISTING_MAX_RESULTS) -> List[Dict[str, Any]]:
    """Fetch the discourses a listing plan describes, in order.

    Chapter order (within-book browsing) sorts server-side on chapter_index;
    date order sorts client-side because the corpus stores dates as text in
    several formats. Returns [] on no matches or any error — the pipeline
    decides how to degrade.
    """
    from weaviate_client import get_client
    from metadata_norm import date_sort_key

    flt = build_article_filter(plan.filters)
    if flt is None:
        return []
    limit = min(plan.limit or limit_default, LISTING_MAX_RESULTS)
    sort = plan.sort or ("chapter" if plan.filters.get("book") else "date")

    try:
        client = get_client()
        articles = client.collections.get("Article")
        if sort == "chapter":
            response = articles.query.fetch_objects(
                filters=flt,
                limit=limit,
                sort=Sort.by_property("chapter_index", ascending=True),
                return_properties=_LISTING_PROPS,
            )
            return [_card(o) for o in response.objects]

        response = articles.query.fetch_objects(
            filters=flt,
            limit=_DATE_SORT_FETCH,
            return_properties=_LISTING_PROPS,
        )
        objs = sorted(response.objects, key=lambda o: date_sort_key((o.properties or {}).get("date", "")))
        return [_card(o) for o in objs[:limit]]
    except Exception as e:
        logging.error(f"list_discourses failed for filters={plan.filters}: {e}")
        return []
