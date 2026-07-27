"""The Collections browsing index — the corpus grouped the way readers browse it.

Feeds the frontend's Collections tab: ``get_collections_index`` groups every
Article into the sections a reader expects (the 15 Vahinis, Sathya Sai Speaks
split by volume, the year-based discourse series, and Chinna Katha), and
``list_collection_chapters`` returns one collection's discourses in reading
order — lean rows without content, since the reader fetches the full text via
``/blog/<id>``.

Cache behavior mirrors ``catalog.py``: one iterator pass over ~1,350 Article
metadata rows, cached per process for ``CATALOG_TTL_SECONDS``; a stale cache is
served over an error. (Named ``collections_index`` to avoid shadowing the
stdlib ``collections``.)
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional

from .config import CATALOG_TTL_SECONDS
from .catalog import VAHINI_BOOKS
from .listing import build_article_filter

SSS_BOOK = "Sathya Sai Speaks"
CHINNA_KATHA = "Chinna Katha"
SERIES_BOOKS = frozenset({
    "Summer Showers", "Sai Echoes from Kodai Hills", "Monsoon Showers",
    "Ati Rudra Maha Yajna", "Women's Role in Rejuvenating the Culture of Bharat",
})
_VAHINI_SET = frozenset(VAHINI_BOOKS)

_cache = {"index": None, "ts": 0.0}


def _slug(*parts) -> str:
    joined = " ".join(str(p) for p in parts if p is not None)
    return re.sub(r"[^a-z0-9]+", "-", joined.lower()).strip("-")


def _build_index() -> Dict[str, Any]:
    from weaviate_client import get_client

    client = get_client()
    articles = client.collections.get("Article")

    vahinis: Dict[str, int] = {}
    sss_years: Dict[int, int] = {}
    sss_undated = 0
    series: Dict[tuple, int] = {}  # (book, year) -> count
    chinna_katha = 0
    strays = 0
    total = 0

    for obj in articles.iterator(return_properties=["book", "volume", "year"]):
        props = obj.properties or {}
        book = (props.get("book") or "").strip()
        total += 1
        if book in _VAHINI_SET:
            vahinis[book] = vahinis.get(book, 0) + 1
        elif book == SSS_BOOK:
            # SSS is browsed by year (the source site has no volume structure
            # for most of the corpus). Discourses without a date fall into a
            # single "Undated" entry so they stay reachable; their volume /
            # discourse-number metadata shows on the reader page.
            year = props.get("year")
            if year is None:
                sss_undated += 1
            else:
                sss_years[int(year)] = sss_years.get(int(year), 0) + 1
        elif book in SERIES_BOOKS:
            year = props.get("year")
            if year is None:
                strays += 1
                continue
            key = (book, int(year))
            series[key] = series.get(key, 0) + 1
        elif book == CHINNA_KATHA:
            chinna_katha += 1
        else:
            strays += 1

    if strays:
        logging.warning(f"collections index: {strays} articles excluded (unknown book or missing volume/year)")

    sss_entries = [
        {"key": _slug("sss", year), "title": f"Sathya Sai Speaks {year}",
         "book": SSS_BOOK, "year": year, "count": count}
        for year, count in sorted(sss_years.items())
    ]
    if sss_undated:
        sss_entries.append(
            {"key": "sss-undated", "title": "Sathya Sai Speaks — Undated",
             "book": SSS_BOOK, "undated": True, "count": sss_undated}
        )

    return {
        "sections": {
            "vahinis": [
                {"key": _slug(book), "title": book, "book": book, "count": count}
                for book, count in sorted(vahinis.items())
            ],
            "sathya_sai_speaks": sss_entries,
            "series": [
                {"key": _slug(book, year), "title": f"{book} {year}",
                 "book": book, "year": year, "count": count}
                for (book, year), count in sorted(series.items())
            ],
            "chinna_katha": (
                [{"key": _slug(CHINNA_KATHA), "title": CHINNA_KATHA,
                  "book": CHINNA_KATHA, "count": chinna_katha}]
                if chinna_katha else []
            ),
        },
        "total": total,
    }


def get_collections_index(force: bool = False) -> Dict[str, Any]:
    """The cached grouped index, refreshed at most every CATALOG_TTL_SECONDS.
    Serves a stale cache over an error; raises only when there has never been
    a successful build (the endpoint turns that into a 503)."""
    now = time.time()
    if not force and _cache["index"] is not None and now - _cache["ts"] < CATALOG_TTL_SECONDS:
        return _cache["index"]
    try:
        index = _build_index()
        _cache["index"] = index
        _cache["ts"] = now
        return index
    except Exception as e:
        if _cache["index"] is not None:
            logging.error(f"collections index refresh failed ({e}); serving stale cache")
            return _cache["index"]
        raise


# One fetch covers the whole corpus with headroom — needed both for the
# no-book search-index path and for Sathya Sai Speaks (500+ rows even for a
# single facet like "undated"); per-collection queries just return fewer rows.
_FETCH_LIMIT = 5000


def list_collection_chapters(book: Optional[str] = None, volume: Optional[int] = None,
                             year: Optional[int] = None,
                             undated: bool = False) -> List[Dict[str, Any]]:
    """Discourses in reading order — metadata only, no content.

    With `book`, one collection's chapters; without it, the entire corpus
    (lean rows powering the Collections search bar client-side). `undated`
    keeps only rows with no year — Weaviate can't filter on null without
    null-indexing, so the book's rows are fetched and filtered in Python
    (the "Undated" card in the SSS section). Sorted in Python rather than by
    Weaviate: `chapter_index` can be absent on some series articles (falls
    back to date order), and the server can't sort on a property some
    objects lack.
    """
    from weaviate_client import get_client
    from metadata_norm import date_sort_key

    filters: Dict[str, Any] = {}
    if book:
        filters["book"] = book
    if volume is not None:
        filters["volume"] = volume
    if year is not None:
        filters["year_start"] = filters["year_end"] = year

    client = get_client()
    articles = client.collections.get("Article")
    response = articles.query.fetch_objects(
        filters=build_article_filter(filters),
        limit=_FETCH_LIMIT,
        return_properties=["title", "date", "occasion", "location",
                           "chapter_index", "collection_name",
                           "book", "volume", "year"],
    )

    def sort_key(obj):
        props = obj.properties or {}
        ch = props.get("chapter_index")
        return (props.get("book") or "",
                int(props.get("volume") or 0),
                int(props.get("year") or 0),
                int(ch) if ch is not None else 10**6,
                date_sort_key(props.get("date", "") or ""))

    objects = response.objects
    if undated:
        objects = [o for o in objects if (o.properties or {}).get("year") is None]

    chapters = []
    for obj in sorted(objects, key=sort_key):
        props = obj.properties or {}
        ch = props.get("chapter_index")
        vol = props.get("volume")
        yr = props.get("year")
        chapters.append({
            "id": str(obj.uuid),
            "title": props.get("title", ""),
            "chapter_index": int(ch) if ch is not None else None,
            "date": props.get("date", "") or "",
            "occasion": props.get("occasion", "") or "",
            "location": props.get("location", "") or "",
            "collection": props.get("collection_name", "") or "",
            "book": props.get("book", "") or "",
            "volume": int(vol) if vol is not None else None,
            "year": int(yr) if yr is not None else None,
        })
    return chapters
