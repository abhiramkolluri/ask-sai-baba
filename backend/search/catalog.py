"""Corpus catalog — the normalized metadata vocabulary the query router needs.

The router must map user phrasing ("the Gita Vahini") onto the corpus's
canonical values ("Geeta Vahini"), so its prompt is grounded in the actual
distinct ``book`` values, the year span, and the common location/occasion
keywords. This module fetches those once per process from Weaviate (cheap
aggregates over ~1,350 articles), caches them for ``CATALOG_TTL_SECONDS``, and
NEVER raises — on any failure it serves a static fallback of the known book
families so the router keeps working without Weaviate.

The catalog reads the ``book``/``year`` properties written by
``backfill_metadata.py``; until that has run, the aggregate comes back empty
and the static fallback is served.
"""

import logging
import re
import time
from collections import Counter

from .config import CATALOG_TTL_SECONDS

# Book names must match the corpus exactly (note: "Geeta", not "Gita").
# VAHINI_BOOKS is also the collections-index grouping source (collections_index.py).
VAHINI_BOOKS = [
    "Bhagavata Vahini", "Dharma Vahini", "Dhyana Vahini", "Geeta Vahini",
    "Jnana Vahini", "Leela Kaivalya Vahini", "Prasanthi Vahini",
    "Prashnottara Vahini", "Prema Vahini", "Ramakatha Rasavahini",
    "Sandeha Nivarini", "Sathya Sai Vahini", "Sutra Vahini",
    "Upanishad Vahini", "Vidya Vahini",
]

# Served when Weaviate is unreachable or the backfill has not run yet.
STATIC_BOOKS = VAHINI_BOOKS + [
    "Chinna Katha", "Sathya Sai Speaks", "Summer Showers",
    "Sai Echoes from Kodai Hills", "Monsoon Showers",
    "Ati Rudra Maha Yajna", "Women's Role in Rejuvenating the Culture of Bharat",
]

_STATIC_CATALOG = {
    "books": {name: None for name in STATIC_BOOKS},  # book -> chapter count (unknown)
    "year_min": 1953,
    "year_max": 2006,
    "locations": ["prasanthi nilayam", "brindavan", "kodaikanal", "anantapur"],
    "occasions": ["dasara", "shivaratri", "birthday", "summer course", "convocation"],
    "static": True,
}

_TOKEN_RE = re.compile(r"[a-z]+")

_cache = {"catalog": None, "ts": 0.0}


def _keyword_counts(values):
    """Lowercase word tokens across a list of raw values ('Brindavan, KA' ->
    brindavan, ka), dropping state-code noise and one-letter fragments."""
    counts = Counter()
    for value in values:
        for tok in _TOKEN_RE.findall((value or "").lower()):
            if len(tok) > 2:
                counts[tok] += 1
    return counts


def _fetch_catalog():
    from weaviate_client import get_client

    client = get_client()
    articles = client.collections.get("Article")

    books = {}       # book -> article/chapter count
    years = []
    locations = []
    occasions = []
    # One iterator pass over metadata-only props: with ~1,350 articles this is
    # a couple of round trips, and it gathers everything the prompt needs
    # (per-book counts, year span, location/occasion keyword vocabulary).
    for obj in articles.iterator(return_properties=["book", "year", "location", "occasion"]):
        props = obj.properties or {}
        book = (props.get("book") or "").strip()
        if book:
            books[book] = books.get(book, 0) + 1
        if props.get("year"):
            years.append(int(props["year"]))
        if props.get("location"):
            locations.append(props["location"])
        if props.get("occasion"):
            occasions.append(props["occasion"])

    if not books:  # backfill has not run yet — the static list is more useful
        raise RuntimeError("no `book` values in Article; run backfill_metadata.py")

    return {
        "books": books,
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "locations": [t for t, _ in _keyword_counts(locations).most_common(30)],
        "occasions": [t for t, _ in _keyword_counts(occasions).most_common(40)],
        "static": False,
    }


def get_catalog(force=False):
    """The cached catalog, refreshed at most every CATALOG_TTL_SECONDS.
    Never raises; degrades to the static fallback (not cached, so a later call
    retries the real fetch after the TTL)."""
    now = time.time()
    if not force and _cache["catalog"] is not None and now - _cache["ts"] < CATALOG_TTL_SECONDS:
        return _cache["catalog"]
    try:
        catalog = _fetch_catalog()
        _cache["catalog"] = catalog
        _cache["ts"] = now
        return catalog
    except Exception as e:
        logging.error(f"get_catalog failed ({e}); serving static fallback")
        return _STATIC_CATALOG


def canonical_book(name, catalog=None):
    """Case-insensitive match of a router-emitted book name against the
    catalog; returns the canonical spelling or None when unknown."""
    if not name or not isinstance(name, str):
        return None
    catalog = catalog or get_catalog()
    by_lower = {b.lower(): b for b in catalog["books"]}
    return by_lower.get(name.strip().lower())


def format_catalog_for_prompt(catalog=None):
    """Compact catalog block for the router's system prompt."""
    catalog = catalog or get_catalog()
    books = sorted(catalog["books"])
    lines = ["Known books (use EXACTLY these strings for `book`):"]
    lines.append(", ".join(books))
    lines.append(
        '"Sathya Sai Speaks" is a multi-volume discourse anthology — set `volume` '
        "when the user names one. The year-based series (Summer Showers, Sai Echoes "
        "from Kodai Hills, etc.) are filtered by `year_start`/`year_end`, not by volume."
    )
    if catalog.get("year_min") and catalog.get("year_max"):
        lines.append(
            f"Dated discourses span {catalog['year_min']}-{catalog['year_max']}; "
            "the Vahinis and Chinna Katha are undated books."
        )
    if catalog.get("locations"):
        lines.append("Common location keywords: " + ", ".join(catalog["locations"][:20]))
    if catalog.get("occasions"):
        lines.append("Common occasion keywords: " + ", ".join(catalog["occasions"][:25]))
    return "\n".join(lines)
