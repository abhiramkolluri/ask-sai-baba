"""Listing route (Router v2): return a named collection's chapters IN ORDER.

For enumeration questions like "return the first 5 chapters from Prema Vahini",
users want the chapters in reading order — not a relevance-ranked thematic search.
The corpus already carries the ordering fields from the structured-search backfill
(`chapter_index` 0-based, `book`, `volume`, `year` on each Article), so this is a
filter-and-sort, not a search. If the named collection isn't found we abstain
honestly rather than semanticize.

Ordering mirrors the (unmerged) collections_index.py sort key:
    (book, volume, year, chapter_index, date)
so series without a chapter_index (Summer Showers, etc.) still order by date.
"""

import re
import logging

from weaviate_client import get_client
from weaviate.classes.query import Filter
from .config import LISTING_DEFAULT, LISTING_MAX, WEAVIATE_RETRY_ATTEMPTS
from .resilience import with_retries

# Article properties we read for a chapter listing.
_PROPS = ["title", "content", "date", "occasion", "location", "link",
          "collection_name", "book", "volume", "year", "chapter_index"]


def _date_sort_key(date_str):
    """Best-effort numeric key from a date string so undated items sort last.
    Pulls a 4-digit year; falls back to a large number when absent."""
    m = re.search(r"\b(\d{4})\b", date_str or "")
    return int(m.group(1)) if m else 10 ** 6


def _sort_key(props):
    ch = props.get("chapter_index")
    return (
        (props.get("book") or props.get("collection_name") or "").lower(),
        int(props.get("volume") or 0),
        int(props.get("year") or 0),
        int(ch) if ch is not None else 10 ** 6,  # chapters in order; undated last
        _date_sort_key(props.get("date", "") or ""),
    )


def _first_sentences(text, n=2, cap=240):
    """Opening 1-2 sentences of a chapter as a preview (not an answering quote)."""
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(parts[:n])[:cap]


def _to_result(props, uuid):
    content = props.get("content", "") or ""
    return {
        "_id": str(uuid),
        "title": props.get("title", ""),
        "matched_passage": content,
        "passage_index": 0,
        "content": content,
        "best_sentence": _first_sentences(content),  # preview, listing isn't Q&A
        "score": 1.0,
        "location": props.get("location", ""),
        "occasion": props.get("occasion", ""),
        "link": props.get("link", ""),
        "collection": props.get("collection_name", ""),
        "date_authored": props.get("date", ""),
        "chapter_index": props.get("chapter_index"),  # frontend shows "Chapter N"
    }


def _translit_key(name):
    """Collapse a romanized title to a comparison key.

    Sanskrit/Telugu transliteration varies mostly in vowels and in a trailing 'h'
    after consonants — Gita / Geeta / Geetha are the same book. Dropping vowels
    and h leaves "gtvhn" for all three, so they compare equal.
    """
    return re.sub(r"[^a-z]", "", re.sub(r"[aeiouh]", "", (name or "").lower()))


def _known_collection_names():
    """Distinct collection/book names in the corpus. Only called on a lookup miss."""
    def _fetch_names():
        client = get_client()
        if not client:
            raise RuntimeError("Weaviate client unavailable for collection resolve")
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
                            what="Collection name resolve")
    except Exception as e:
        logging.warning(f"collection-name resolve failed: {e}")
        return set()


def _resolve_collection_name(collection):
    """Map a loosely-spelled collection to the corpus's own spelling, or None."""
    want = _translit_key(collection)
    if not want:
        return None
    best = None
    for name in _known_collection_names():
        key = _translit_key(name)
        # Equal keys, or the query is a clean prefix/substring of a longer title
        # ("Prema Vahini" inside "Prema Vahini Vol 2").
        if key == want or (len(want) >= 4 and want in key):
            if best is None or len(name) < len(best):
                best = name
    return best


def list_collection(collection, count=None, order="first"):
    """Return a collection's chapters in reading order.

    Returns {"status": "found"|"not_found", "collection": <resolved name>|None,
    "results": [...]}. Raises PipelineServiceError on a Weaviate failure (so the
    caller surfaces an honest service error, never a false empty)."""
    if not collection:
        return {"status": "not_found", "collection": None, "results": []}

    def _fetch():
        client = get_client()
        if not client:
            raise RuntimeError("Weaviate client unavailable for listing")
        articles = client.collections.get("Article")
        # Match the named collection on `book` OR `collection_name` (users spell it
        # loosely). Fetch the whole book (a few hundred at most) and order locally.
        f = (Filter.by_property("book").like(f"*{collection}*")
             | Filter.by_property("collection_name").like(f"*{collection}*"))
        resp = articles.query.fetch_objects(filters=f, limit=LISTING_MAX * 4)
        return resp.objects

    objs = with_retries(_fetch, attempts=WEAVIATE_RETRY_ATTEMPTS, what="Collection listing")

    if not objs:
        # The substring filter above is exact, and romanized titles are not: the
        # corpus spells it "Geeta Vahini" while users (and the router) write
        # "Gita Vahini" or "Geetha Vahini". None of those substring-match each
        # other, so a correctly-identified collection returned not_found and the
        # user was told we don't have a book we do have.
        #
        # Only on a miss, resolve against the real collection names by closest
        # match on a vowel-stripped key ("gitavahini" ~ "geetavahini"). Costs one
        # extra read, and never runs on the happy path.
        alt = _resolve_collection_name(collection)
        if alt and alt.lower() != collection.lower():
            logging.info(f"listing: {collection!r} resolved to corpus spelling {alt!r}")
            collection = alt
            objs = with_retries(_fetch, attempts=WEAVIATE_RETRY_ATTEMPTS,
                                what="Collection listing (resolved)")

    if not objs:
        return {"status": "not_found", "collection": None, "results": []}

    objs = sorted(objs, key=lambda o: _sort_key(o.properties or {}))
    resolved = (objs[0].properties or {}).get("book") or (objs[0].properties or {}).get("collection_name")

    n = count if isinstance(count, int) and count > 0 else LISTING_DEFAULT
    if order == "all":
        chosen = objs[:LISTING_MAX]
    elif order == "last":
        chosen = objs[-n:]  # tail, still in reading order
    else:  # "first"
        chosen = objs[:n]

    results = [_to_result(o.properties or {}, o.uuid) for o in chosen]
    return {"status": "found", "collection": resolved, "results": results}
