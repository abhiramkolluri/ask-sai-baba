"""Pure normalization helpers for discourse metadata.

The corpus's `collection_name` values are inconsistent: the 15 Vahinis and
Chinna Katha are clean book names, but every Sathya Sai Speaks discourse carries
its own name ('SSS, Vol 14Disc. 10'), and several book families embed their year
('Summer Showers 1976'). These helpers derive the normalized `book` / `volume` /
`chapter_index` / `year` properties that the search filters and the listing path
rely on.

Shared by backfill_metadata.py, ingest_vahinis.py, chunk_articles.py, and
search/listing.py. No Weaviate or OpenAI dependencies — keep it importable
everywhere without side effects.
"""

import re

# 'SSS, Vol 14Disc. 10' (no space between volume and 'Disc.') and tolerant
# variants like 'SSS, Vol 8Disc. 3* alternative translation'.
_SSS_RE = re.compile(r"^SSS,?\s*Vol\.?\s*(\d+)\s*Disc\.?\s*(\d+)", re.IGNORECASE)

# 'Summer Showers 1976', 'Sai Echoes from Kodai Hills 1992', etc.
_YEAR_SUFFIX_RE = re.compile(r"^(.*?)\s+((?:19|20)\d{2})$")

_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

SSS_BOOK = "Sathya Sai Speaks"


def normalize_collection(collection_name):
    """Derive (book, volume, chapter_index, year) from a raw collection_name.

    Any element that cannot be derived is None. chapter_index is 0-based (the
    scraped corpora are 0-based; SSS 'Disc. N' is 1-based, so N-1 is stored).
    """
    name = (collection_name or "").strip()
    if not name:
        return (None, None, None, None)

    m = _SSS_RE.match(name)
    if m:
        return (SSS_BOOK, int(m.group(1)), int(m.group(2)) - 1, None)

    m = _YEAR_SUFFIX_RE.match(name)
    if m:
        return (m.group(1).strip(), None, None, int(m.group(2)))

    return (name, None, None, None)


def extract_year(date_str):
    """First 4-digit year in a date string ('17 October 1953', 'Apr 10, 1993',
    'May, 1972', '2006'), or None."""
    m = _YEAR_RE.search(date_str or "")
    return int(m.group(1)) if m else None


def date_sort_key(date_str):
    """Sortable (year, month, day) tuple across every date format in the corpus:
    '17 October 1953', 'April 1957', 'Apr 10, 1993', 'May, 1972', '2006'.
    Unparseable/empty dates sort last.
    """
    if not date_str or not date_str.strip():
        return (9999, 99, 99)
    parts = date_str.replace(",", " ").split()

    def month_of(token):
        return _MONTHS.get(token[:3].lower())

    try:
        if len(parts) == 3:
            if parts[0].isdigit():  # '17 October 1953'
                return (int(parts[2]), month_of(parts[1]) or 0, int(parts[0]))
            # 'Apr 10, 1993'
            return (int(parts[2]), month_of(parts[0]) or 0, int(parts[1]))
        if len(parts) == 2:  # 'April 1957' / 'May, 1972'
            return (int(parts[1]), month_of(parts[0]) or 0, 0)
        if len(parts) == 1 and parts[0].isdigit():  # '2006'
            return (int(parts[0]), 0, 0)
    except (ValueError, TypeError):
        pass
    year = extract_year(date_str)
    return (year, 0, 0) if year else (9999, 99, 99)


_SLUG_STOPWORDS = frozenset({
    "the", "a", "an", "of", "and", "to", "in", "is", "for", "with",
    "on", "at", "by", "your", "his", "her", "our", "their", "it", "be",
})


def slug_key(text):
    """Stopword-stripped kebab key for fuzzy title/slug matching across sites
    (saispeaks slugs drop articles/prepositions that ssssahitya keeps):
    'Women Are The Embodiments Of Nobility' and
    'women-are-embodiments-nobility' both -> 'women-are-embodiments-nobility'.
    """
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return "-".join(w for w in words if w not in _SLUG_STOPWORDS)


def derive_article_metadata(collection_name, date_str, crawler_chapter_index=None):
    """Full derivation for one article: dict with book/volume/chapter_index/year
    (values may be None). The crawler's chapter_index (when the article's link is
    in a scraped JSON) wins over anything parsed from the collection_name.
    """
    book, volume, chapter_index, year_from_name = normalize_collection(collection_name)
    year = extract_year(date_str) or year_from_name
    if crawler_chapter_index is not None and crawler_chapter_index != "":
        chapter_index = int(crawler_chapter_index)
    return {"book": book, "volume": volume, "chapter_index": chapter_index, "year": year}
